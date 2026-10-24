"""A vault's identity (WP04 M1): a random UUID in <vault>/.nuru/vault-id, so it survives the vault being
moved or renamed, unlike the path hash protocol v1 uses (protocol.vault_id). Each computer keeps a
registry of where it last saw each UUID (~/.nuru/vaults/registry.json, or NURU_VAULTS_DIR).

A vault whose UUID is registered at another path that still exists and still holds that UUID is a copy:
it isn't served until the user says which it is (rebind: the same vault, the other copy is stale; clone:
a new vault with its own UUID). A UUID whose old path is gone or holds another ID was moved, and the
registry just follows it.

The file is created by the service the first time it serves a vault, and read by the Obsidian plugin.

The registry is authoritative, so it fails closed: a missing file is an empty registry, but one that
can't be read or isn't a registry stops everything that needs it (RegistryError), and is left as it is
for the user to look at. Every read-modify-write holds registry.lock, so services for different vaults
and the CLI never lose each other's entries.
"""

import contextlib
import json
import os
import shlex
import tempfile
import time
import uuid as uuidlib
from pathlib import Path

LOCK_SECONDS = 10

ID_FILE = Path(".nuru") / "vault-id"


def vaults_dir() -> Path:
    return Path(os.environ.get("NURU_VAULTS_DIR") or Path.home() / ".nuru" / "vaults")


class RegistryError(RuntimeError):
    """registry.json exists but can't be read as a registry: nothing is decided from it."""


class VaultConflict(RuntimeError):
    """The vault's UUID belongs to another live copy of it."""

    def __init__(self, vault: Path, vault_uuid: str, other: Path) -> None:
        quoted = shlex.quote(str(vault))
        super().__init__(
            f"{vault} has the same Nuru vault ID as {other}, which still exists: one is a copy of the other. "
            f"If {vault} is the one to keep (the other is an old copy), run `uv run nuru-serve vault rebind {quoted}`; "
            f"if it's a separate vault, run `uv run nuru-serve vault clone {quoted}` to give it its own ID and history.")
        self.vault, self.uuid, self.other = vault, vault_uuid, other


def read(vault: Path) -> str | None:
    """The vault's UUID, or None if it has none (never served) or the file isn't a UUID."""
    try:
        text = (Path(vault) / ID_FILE).read_text(encoding="utf-8").strip()
        return str(uuidlib.UUID(text)) if len(text) == 36 else None
    except (OSError, ValueError):
        return None


def _write(vault: Path, vault_uuid: str, *, replace: bool) -> None:
    path = Path(vault) / ID_FILE
    path.parent.mkdir(exist_ok=True)
    if replace:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(vault_uuid + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return
    with open(path, "x", encoding="utf-8") as file:  # never over an ID another process has just written
        file.write(vault_uuid + "\n")


@contextlib.contextmanager
def _exclusive(path: Path, seconds: float = LOCK_SECONDS, busy: Exception | None = None):
    """Hold an exclusive lock on `path`, waiting up to `seconds` for another process to let go, then
    raising `busy` (a RegistryError by default). The OS releases it if the holder dies."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = open(path, "a+", encoding="utf-8")
    deadline = time.monotonic() + seconds
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise busy or RegistryError(f"{path} is held by another Nuru for more than {seconds:g} s") from None
                time.sleep(0.05)
        yield
    finally:
        handle.close()  # releases the lock


class Registry:
    def __init__(self, directory: Path | None = None) -> None:
        self.path = Path(directory or vaults_dir()) / "registry.json"
        self.lock = self.path.with_name("registry.lock")

    def entries(self) -> dict[str, str]:
        """{uuid: last-seen path}. Empty only when there's no registry yet; RegistryError otherwise."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            raise RegistryError(f"{self.path} can't be read ({type(error).__name__}); it's left as it is. "
                                "Fix or move it aside, then start Nuru again") from None
        if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
            raise RegistryError(f"{self.path} isn't a Nuru vault registry; it's left as it is. "
                                "Fix or move it aside, then start Nuru again")
        return data

    def _save(self, entries: dict[str, str]) -> None:
        """Replace the registry in one step, from a temporary file of its own (callers hold the lock)."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix="registry.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as file:
                file.write(json.dumps(entries, indent=2, sort_keys=True) + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def identify(self, vault: Path) -> str:
        """The vault's UUID, made if it has none, after checking it isn't a copy (VaultConflict)."""
        with _exclusive(self.lock):
            return self._identify(Path(vault).resolve())

    def _identify(self, vault: Path) -> str:
        vault_uuid = read(vault)
        if vault_uuid is None:
            try:
                _write(vault, str(uuidlib.uuid4()), replace=False)
            except FileExistsError:
                pass  # another process made it first, or the file isn't a UUID: read decides
            vault_uuid = read(vault)
            if vault_uuid is None:
                raise ValueError(f"{vault / ID_FILE} isn't a Nuru vault ID; remove it to make a new one")
        entries = self.entries()
        known = entries.get(vault_uuid)
        if known and Path(known) != vault and read(Path(known)) == vault_uuid:
            raise VaultConflict(vault, vault_uuid, Path(known))
        if known != str(vault):
            entries[vault_uuid] = str(vault)  # first seen, or moved
            self._save(entries)
        return vault_uuid

    def rebind(self, vault: Path) -> str:
        """`vault` keeps its UUID (and so its history); the other copy is the stale one."""
        vault = Path(vault).resolve()
        with _exclusive(self.lock):
            vault_uuid = read(vault)
            if vault_uuid is None:
                raise ValueError(f"{vault} has no Nuru vault ID")
            entries = self.entries()
            entries[vault_uuid] = str(vault)
            self._save(entries)
        return vault_uuid

    def clone(self, vault: Path) -> str:
        """`vault` gets a new UUID: a separate vault, with an empty history.

        The registry entry is committed before the vault's file changes. Interrupted between the two,
        the vault still holds its old UUID, so it's still refused as a copy and `clone` can simply run
        again; the unused new entry points at a vault that doesn't hold its UUID, which counts as gone."""
        vault = Path(vault).resolve()
        vault_uuid = str(uuidlib.uuid4())
        with _exclusive(self.lock):
            entries = self.entries()
            entries[vault_uuid] = str(vault)
            self._save(entries)
            _write(vault, vault_uuid, replace=True)
        return vault_uuid
