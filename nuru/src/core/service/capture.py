"""Model-free, durable Inbox captures for the WP02 local service.

M3 supplies the remote listener and scoped credentials. This writer deliberately
accepts a server-derived device ID, never one from the request body.
"""

import hashlib
import errno
import os
import secrets
import stat
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from obsidian_mcp import mutations
from ulid import ULID

from core.service import remote_contract

MAX_RECORDS = 100_000
MAX_DEVICE_RECORDS = 20_000
PER_DEVICE_PER_MINUTE = 60
MAX_IN_FLIGHT = 8
RECEIPT_DAYS = 30
# Publishing pins directories by descriptor (dir_fd), which Windows lacks: there no capture is accepted,
# and one left pending by another computer (a moved database) is left as it is and reported unavailable.
_WINDOWS = os.name == "nt"


class CaptureError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(code)


class _LinkUnavailable(OSError):
    """The vault filesystem cannot publish with a provenance-preserving link."""


def _note(uid: str, content: str) -> bytes:
    return f'---\nuid: "{uid}"\n---\n{content}'.encode("utf-8")


def _read_bounded(vault: Path, path: Path) -> bytes:
    with mutations.parent(vault, path) as (fd, name):
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, "rb") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > remote_contract.MAX_CAPTURE_BYTES + 256:
                raise ValueError("capture target is not a bounded regular file")
            return file.read(remote_contract.MAX_CAPTURE_BYTES + 257)


class CaptureWriter:
    def __init__(self, store, vault: Path, vault_id: str, lock):
        self.store, self.vault, self.vault_id, self.lock = store, Path(vault), vault_id, lock
        self._active: set[str] = set()
        self._idle = threading.Condition(lock)
        self._closing = False
        self.max_records = self._limit("NURU_CAPTURE_MAX_RECORDS", MAX_RECORDS)
        self.max_device_records = self._limit("NURU_CAPTURE_MAX_DEVICE_RECORDS", MAX_DEVICE_RECORDS)
        self._next_sweep = 0.0
        self._link_available: bool | None = None

    def _probe_links(self) -> None:
        if self._link_available is not None:
            return
        if _WINDOWS:
            self._link_available = False
            return
        name = f".nuru-link-probe-{secrets.token_hex(8)}"
        stage = name + ".tmp"
        try:
            with mutations.coordinate(self.vault):
                with mutations.parent(self.vault, self.vault / "Inbox" / name, create=True) as (fd, _):
                    created_stage = created_target = False
                    try:
                        file_fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                          0o600, dir_fd=fd)
                        os.close(file_fd)
                        created_stage = True
                        os.link(stage, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                        created_target = True
                        os.fsync(fd)
                        self._link_available = True
                    finally:
                        if created_target:
                            os.unlink(name, dir_fd=fd)
                        if created_stage:
                            os.unlink(stage, dir_fd=fd)
                        os.fsync(fd)
        except (OSError, ValueError):
            self._link_available = False

    @staticmethod
    def _limit(name: str, default: int) -> int:
        value = int(os.environ.get(name, str(default)))
        if value < 1:
            raise ValueError(f"{name} must be positive")
        return value

    def _sweep(self) -> None:
        if time.monotonic() < self._next_sweep:
            return
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=RECEIPT_DAYS)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        legacy_cutoff = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        with self.store.transaction():
            self.store.expire_capture_receipts(cutoff)
            self.store.delete_completed_legacy(legacy_cutoff)
        self._next_sweep = time.monotonic() + 3600

    def stop(self, timeout: float) -> bool:
        with self.lock:
            self._closing = True
            return self._idle.wait_for(lambda: not self._active, timeout)

    def _reconcile(self, key: str, record: dict) -> dict:
        if record["state"] not in {"accepted", "staging", "running", "uncertain"}:
            return record
        try:
            actual = _read_bounded(self.vault, self.vault / record["path"])
        except FileNotFoundError:
            clean = True
            if record["state"] == "staging":
                # The transition to running commits only after stage fsync and
                # before link. In staging, publication could not have begun.
                clean = self._discard_unpublished_stage(record)
            # After the running transition, an external editor may have moved
            # or deleted a published note. Absence is then ambiguous.
            state = "accepted" if clean and record["state"] in {"accepted", "staging"} else "uncertain"
            self.store.set_capture(key, state)
        except (OSError, ValueError):
            self.store.set_capture(key, "uncertain")
        else:
            if (record["state"] in {"accepted", "staging"} or
                    hashlib.sha256(actual).hexdigest() != record["note_hash"] or
                    not self._published_from_stage(record)):
                self.store.set_capture(key, "uncertain")
            else:
                self.store.set_capture(key, "completed", self._receipt(record))
        return self.store.capture(key)

    @staticmethod
    def _stage_name(record: dict) -> str:
        return f'.nuru-capture-{record["uid"]}.tmp'

    def _discard_unpublished_stage(self, record: dict) -> bool:
        try:
            with mutations.parent(self.vault, self.vault / record["path"]) as (fd, _):
                os.unlink(self._stage_name(record), dir_fd=fd)
                os.fsync(fd)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            return False
        return True

    def _published_from_stage(self, record: dict) -> bool:
        try:
            with mutations.parent(self.vault, self.vault / record["path"]) as (fd, name):
                target = os.stat(name, dir_fd=fd, follow_symlinks=False)
                stage = os.stat(self._stage_name(record), dir_fd=fd, follow_symlinks=False)
                return (stat.S_ISREG(target.st_mode) and stat.S_ISREG(stage.st_mode) and
                        (target.st_dev, target.st_ino) == (stage.st_dev, stage.st_ino))
        except (OSError, ValueError):
            return False

    def _remove_stage(self, record: dict) -> None:
        try:
            with mutations.parent(self.vault, self.vault / record["path"]) as (fd, _):
                if not self._published_from_stage(record):
                    return
                os.unlink(self._stage_name(record), dir_fd=fd)
                os.fsync(fd)
        except (FileNotFoundError, OSError, ValueError):
            pass  # The committed receipt is sufficient; orphan cleanup is best effort.

    def _publish(self, record: dict, content: str) -> None:
        data = _note(record["uid"], content)
        with mutations.coordinate(self.vault):
            with mutations.parent(self.vault, self.vault / record["path"], create=True) as (fd, name):
                stage = self._stage_name(record)
                try:
                    file_fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                                      dir_fd=fd)
                except FileExistsError:
                    # A crash during staging may leave partial bytes. The DB is
                    # still in staging, so publication has not begun; discard it.
                    staged = _read_bounded(self.vault, self.vault / "Inbox" / stage)
                    if staged != data:
                        os.unlink(stage, dir_fd=fd)
                        file_fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                          0o600, dir_fd=fd)
                        with os.fdopen(file_fd, "wb") as file:
                            file.write(data)
                            file.flush()
                            os.fsync(file.fileno())
                else:
                    with os.fdopen(file_fd, "wb") as file:
                        file.write(data)
                        file.flush()
                        os.fsync(file.fileno())
                with self.lock, self.store.transaction():
                    self.store.set_capture(remote_contract.capture_key(record["device_id"], self.vault_id,
                                                                       record["request_id"]), "running")
                try:
                    os.link(stage, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                except OSError as error:
                    if error.errno in {errno.EOPNOTSUPP, errno.ENOSYS, errno.EPERM, errno.EMLINK, errno.EXDEV}:
                        raise _LinkUnavailable() from error
                    raise
                os.fsync(fd)

    @staticmethod
    def _receipt(record: dict) -> dict:
        return {"ok": True, "uid": record["uid"], "path": record["path"],
                "revision": record["note_hash"]}

    def recover(self) -> None:
        with self.lock:
            self._probe_links()
            for key in [] if _WINDOWS else self.store.pending_captures():
                with self.store.transaction():
                    record = self._reconcile(key, self.store.capture(key))
                if record["state"] == "completed":
                    self._remove_stage(record)
            self._next_sweep = 0.0
            self._sweep()

    def status(self, device_id: str, request_id: str) -> tuple[int, dict]:
        key = remote_contract.capture_key(device_id, self.vault_id, request_id)
        with self.lock:
            self._sweep()
            record = self.store.capture(key)
            if record is None:
                raise CaptureError(404, "not_found")
            state = record["state"]
            if _WINDOWS and state not in {"completed", "gone"}:
                raise CaptureError(503, "capture_unavailable")
            if state == "staging":
                state = "running" if key in self._active else "accepted"
            body = {"v": 1, "request_id": request_id, "state": state}
            if state == "completed":
                body["receipt"] = record["receipt"]
            return 200, body

    def create(self, device_id: str, payload: dict) -> tuple[int, dict]:
        content = payload["content"]
        legacy = "request_id" not in payload
        request_id = payload.get("request_id") or f"legacy-{ULID()}"
        key = remote_contract.capture_key(device_id, self.vault_id, request_id)
        digest = remote_contract.capture_hash(content)
        with self.lock:
            self._sweep()
            if self._closing:
                raise CaptureError(503, "stopping")
            record = self.store.capture(key)
            if record:
                if record["payload_hash"] != digest:
                    raise CaptureError(409, "conflict")
                if record["state"] == "completed":
                    return 201, record["receipt"]
                if record["state"] == "gone":
                    raise CaptureError(410, "gone")
                if record["state"] == "uncertain":
                    return 202, {"v": 1, "request_id": request_id, "state": "uncertain",
                                 "status": f"/v1/captures/requests/{request_id}"}
                if key in self._active:
                    return 202, {"v": 1, "request_id": request_id, "state": "running",
                                 "status": f"/v1/captures/requests/{request_id}"}
            self._probe_links()
            if not self._link_available:
                raise CaptureError(503, "capture_unavailable")
            if len(self._active) >= MAX_IN_FLIGHT:
                raise CaptureError(429, "capture_queue_full")
            if record is None:
                if self.store.capture_count() >= self.max_records:
                    raise CaptureError(503, "capture_capacity")
                if self.store.capture_count(device_id) >= self.max_device_records:
                    raise CaptureError(503, "capture_capacity")
                since = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                if self.store.recent_device_captures(device_id, since) >= PER_DEVICE_PER_MINUTE:
                    raise CaptureError(429, "capture_quota")
                uid = str(ULID())
                path = f"Inbox/{uid}.md"
                note_hash = hashlib.sha256(_note(uid, content)).hexdigest()
                with self.store.transaction():
                    self.store.add_capture(key, device_id, self.vault_id, request_id, digest, uid, path,
                                           note_hash, legacy=legacy)
                record = self.store.capture(key)
            # A prior crash may have left a committed intent without a file.
            # Preserve its UID/path and use the retried content only after the
            # canonical hash has matched.
            with self.store.transaction():
                self.store.set_capture(key, "staging")
            self._active.add(key)
        try:
            try:
                self._publish(record, content)
            except _LinkUnavailable:
                with self.lock, self.store.transaction():
                    self.store.set_capture(key, "staging")
                    record = self._reconcile(key, self.store.capture(key))
                    self._link_available = False
                raise CaptureError(503, "capture_unavailable") from None
            except FileExistsError:
                # Another writer owned this path before our publish. Its bytes,
                # even if identical, cannot establish our creation receipt.
                with self.lock, self.store.transaction():
                    self.store.set_capture(key, "uncertain")
                raise CaptureError(500, "capture_failed") from None
            except (OSError, ValueError):
                # The write may have crossed the filesystem boundary. Reconcile
                # from its committed phase before answering; a staging failure
                # could not have published and may safely retry its identity.
                with self.lock, self.store.transaction():
                    record = self._reconcile(key, self.store.capture(key))
                if record["state"] == "completed":
                    self._remove_stage(record)
                if record["state"] != "completed":
                    raise CaptureError(500, "capture_failed")
            receipt = self._receipt(record)
            with self.lock, self.store.transaction():
                self.store.set_capture(key, "completed", receipt)
            self._remove_stage(record)
            return 201, receipt
        finally:
            with self.lock:
                self._active.discard(key)
                self._idle.notify_all()
