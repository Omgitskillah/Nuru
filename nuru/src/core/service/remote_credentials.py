"""Scoped remote credentials. Only hashes persist; onboarding secrets die on restart.

The local operator creates/rotates devices. Remote callers can only redeem an
already approved grant, inspect their session, or log out. This store is separate
from WP01 tokens: neither listener can accept the other's credential class.
"""

import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.service.credentials import PRINCIPAL, _CLIENT_NAME, _hash

SCOPES = frozenset({"capture:create", "chat:use", "notes:read", "notes:write"})
COOKIE = "__Host-nuru"
DAY = 86400
PAIR_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # 32 symbols, no I/L/O/U
PAIR_FAILURE_LIMIT = 10
PAIR_FAILURE_WINDOW = 60


class PairingLimited(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__("pairing failure limit reached")


def _date(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds") if stamp else None


@dataclass(frozen=True)
class Device:
    name: str
    device_id: str
    scopes: frozenset[str]
    kind: str
    digest: str
    principal: str = PRINCIPAL


def csrf(token: str) -> str:
    return hashlib.sha256(("nuru-csrf:" + token).encode()).hexdigest()


class RemoteCredentials:
    def __init__(self, directory: Path, *, clock=time.time):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        path = directory / "devices.db"
        fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS devices (
            name TEXT PRIMARY KEY, device_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
            scopes TEXT NOT NULL, digest TEXT NOT NULL, expires REAL NOT NULL,
            idle REAL NOT NULL, generation TEXT NOT NULL)""")
        self.db.commit()
        self.lock = threading.RLock()
        self.clock = clock
        self.pair_failures = deque()  # shared across callers; never trust proxy/IP headers
        self.rates = {}  # device ID -> recent authenticated request timestamps
        self.pending = {}  # hashed pairing/setup secret -> (expiry, name, generation, kind, data)

    def admit(self, device: Device) -> bool:
        """Bound authenticated request rate before resource lookup or body reads."""
        with self.lock:
            now = self.clock()
            self.rates = {key: value for key, value in self.rates.items() if value and value[-1] > now - 60}
            recent = self.rates.setdefault(device.device_id, deque())
            while recent and recent[0] <= now - 60:
                recent.popleft()
            if len(recent) >= 120:
                return False
            recent.append(now)
            return True

    def close(self):
        with self.lock:
            self.pending.clear()
            self.db.close()

    def _prune(self):
        now = self.clock()
        self.pending = {key: value for key, value in self.pending.items() if value[0] > now}

    def issue(self, name: str, kind: str, scopes=(), *, legacy: str | None = None) -> str:
        """Host confirmation: browser returns a pairing code; shortcut returns bearer.

        Reissuing a name rotates its authority but keeps capture ownership. Removing
        and re-adding the name deliberately creates a new device identity.
        """
        if not _CLIENT_NAME.fullmatch(name):
            raise ValueError("device name must be 1-32 lowercase letters, digits, - or _")
        scopes = frozenset(scopes)
        if kind not in {"browser", "shortcut"} or not scopes <= SCOPES:
            raise ValueError("invalid device kind or scopes")
        if kind == "shortcut":
            if scopes != {"capture:create"}:
                raise ValueError("shortcuts have capture:create only")
        elif not scopes or ("notes:write" in scopes and not {"chat:use", "notes:read"} <= scopes):
            raise ValueError("browser grants need scopes; writes also require chat:use and notes:read")
        if legacy is not None and (kind != "shortcut" or not re.fullmatch(r"[a-fA-F0-9]{64}", legacy)):
            raise ValueError("legacy import requires a 64-hex capture token")
        with self.lock, self.db:
            self._prune()
            old = self.db.execute("SELECT * FROM devices WHERE name=?", (name,)).fetchone()
            if old is None and self.db.execute("SELECT count(*) FROM devices").fetchone()[0] >= 128:
                raise ValueError("device limit reached; revoke an unused device")
            if kind == "browser":
                # Ten base32 characters = 50 random bits, displayed in two groups.
                # Avoid even an accidental collision with another pending grant.
                while True:
                    secret = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(10))
                    if _hash(secret) not in self.pending:
                        break
            else:
                secret = legacy or secrets.token_hex(32)
            duplicate = self.db.execute("SELECT name FROM devices WHERE digest=? AND name!=?", (_hash(secret), name)).fetchone()
            if duplicate:
                raise ValueError("this capture token already belongs to another device")
            generation = secrets.token_hex(16)
            device_id = old["device_id"] if old else "remote-" + secrets.token_hex(16)
            expires = 0 if legacy or kind == "browser" else self.clock() + 90 * DAY
            digest = _hash(secret) if kind == "shortcut" else ""
            self.db.execute("INSERT OR REPLACE INTO devices VALUES (?,?,?,?,?,?,?,?)",
                            (name, device_id, kind, json.dumps(sorted(scopes)), digest, expires, 0, generation))
            self.pending = {k: v for k, v in self.pending.items() if v[1] != name}
            if kind == "browser":
                self.pending[_hash(secret)] = (self.clock() + 300, name, generation, "pair", None)
            return secret[:5] + "-" + secret[5:] if kind == "browser" else secret

    def revoke(self, name: str) -> bool:
        with self.lock, self.db:
            self.pending = {k: v for k, v in self.pending.items() if v[1] != name}
            return self.db.execute("DELETE FROM devices WHERE name=?", (name,)).rowcount > 0

    def cancel_onboarding(self, name: str):
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if v[1] != name}

    def _describe(self, row):
        expiry = min(row["expires"], row["idle"]) if row["kind"] == "browser" and row["idle"] else row["expires"]
        status = "active"
        if row["kind"] == "browser" and not row["digest"]:
            status = "not_paired"
        elif expiry and expiry <= self.clock():
            status = "expired"
        elif expiry and expiry <= self.clock() + (7 * DAY if row["kind"] == "shortcut" else 3600):
            status = "expires_soon"
        renewal = ("Pair again on the Mac after 24 hours idle or 7 days absolute."
                   if row["kind"] == "browser" else
                   "Imported legacy credential has no expiry; rotate or revoke it on the Mac."
                   if not expiry else "Reissue this device on the Mac and update the shortcut before expiry.")
        return dict(name=row["name"], device_id=row["device_id"], kind=row["kind"],
                    scopes=json.loads(row["scopes"]), expires=row["expires"],
                    expires_at=_date(row["expires"]), idle_expires_at=_date(row["idle"]),
                    status=status, renewal=renewal)

    def describe(self, name, *, digest=None):
        with self.lock:
            row = self.db.execute("SELECT * FROM devices WHERE name=?", (name,)).fetchone()
            return self._describe(row) if row and (digest is None or row["digest"] == digest) else {}

    def list(self):
        with self.lock:
            return [self._describe(row) for row in self.db.execute("SELECT * FROM devices ORDER BY name")]

    def redeem(self, code: str) -> str | None:
        with self.lock, self.db:
            self._prune()
            now = self.clock()
            while self.pair_failures and self.pair_failures[0] <= now - PAIR_FAILURE_WINDOW:
                self.pair_failures.popleft()
            if len(self.pair_failures) >= PAIR_FAILURE_LIMIT:
                raise PairingLimited(max(1, math.ceil(self.pair_failures[0] + PAIR_FAILURE_WINDOW - now)))
            normalized = code.replace("-", "").replace(" ", "").upper()
            digest = _hash(normalized)
            grant = self.pending.get(digest) if re.fullmatch(f"[{PAIR_ALPHABET}]{{10}}", normalized) else None
            row = (self.db.execute("SELECT * FROM devices WHERE name=? AND generation=?", grant[1:3]).fetchone()
                   if grant and grant[3] == "pair" else None)
            if not row:
                self.pair_failures.append(now)
                return None
            del self.pending[digest]
            token = secrets.token_hex(32)
            self.db.execute("UPDATE devices SET digest=?, expires=?, idle=? WHERE name=?",
                            (_hash(token), self.clock() + 7 * DAY, self.clock() + DAY, row["name"]))
            return token

    def authenticate(self, token: str, kind: str, *, touch=True) -> Device | None:
        return self.check_digest(_hash(token), kind, touch=touch)

    def check_digest(self, digest: str, kind: str, *, touch=False) -> Device | None:
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM devices WHERE digest=? AND kind=?", (digest, kind)).fetchone()
            if not row or not digest or (row["expires"] and row["expires"] <= self.clock()):
                return None
            if kind == "browser":
                if row["idle"] <= self.clock():
                    return None
                if touch:
                    self.db.execute("UPDATE devices SET idle=? WHERE name=?", (self.clock() + DAY, row["name"]))
            return Device(row["name"], row["device_id"], frozenset(json.loads(row["scopes"])), kind, digest)

    def logout(self, device: Device):
        with self.lock, self.db:
            self.db.execute("UPDATE devices SET digest='', idle=0 WHERE digest=? AND kind='browser'", (device.digest,))

    def setup(self, name: str, origin: str) -> str:
        # Only newly issued capture credentials enter downloads. Rotation also kills
        # an older setup link and old bearer, but preserves this device's receipts.
        with self.lock:
            token = self.issue(name, "shortcut", {"capture:create"})
            row = self.db.execute("SELECT generation FROM devices WHERE name=?", (name,)).fetchone()
            secret = secrets.token_hex(32)
            from core.service.android_setup import android_shortcut
            self.pending[_hash(secret)] = (self.clock() + 300, name, row[0], "setup", android_shortcut(origin, token))
            return "/v1/android-setup/" + secret + ".json"

    def download(self, secret: str, *, consume: bool) -> dict | None:
        with self.lock:
            self._prune()
            grant = self.pending.get(_hash(secret))
            if not grant or grant[3] != "setup":
                return None
            if consume:
                del self.pending[_hash(secret)]
            return grant[4]
