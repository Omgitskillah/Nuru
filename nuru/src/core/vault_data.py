"""Where a vault's Nuru data lives (WP04 M2), and moving today's data there (M3).

Each vault's conversations, service state, turn logs and semantic index are kept on this computer, in
~/.nuru/vaults/<uuid>/ (core.vault_identity), never in the vault: a vault is often a synced folder,
and live SQLite doesn't survive file sync, nor should history travel with the vault.

    <uuid>/db/conversation.db, db/service.db   the conversation store and the service's state
    <uuid>/logs/                                turn logs and Claude request logs
    <uuid>/semantic.sqlite3                     the semantic index (rebuilt, never moved)
    <uuid>/migration.json                       the state of a move of old data into db/, if one ran

NURU_DB_PATH and NURU_LOG_DIR still choose other places (tests, evals).

Before WP04 every vault's data was mixed in the Nuru folder (nuru/.db, nuru/.logs). adopt_legacy()
moves it into one vault's folder, on the user's explicit say-so, in steps a crash can't leave half done:
the old files are checkpointed and copied into db.staging/, checked, and only then put in place with
one directory rename; `migration.json` records how far it got and recover() finishes or undoes it.
"""

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from core.vault_identity import vaults_dir

LEGACY_ROOT = Path(__file__).resolve().parents[2]  # the nuru/ folder
LEGACY_DB = LEGACY_ROOT / ".db"
LEGACY_LOGS = LEGACY_ROOT / ".logs"
DB_FILES = ("conversation.db", "service.db")
LEGACY_SUFFIX = ".pre-wp04"


def data_dir(vault_uuid: str) -> Path:
    path = vaults_dir() / vault_uuid
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def db_dir(vault_uuid: str) -> Path:
    path = data_dir(vault_uuid) / "db"
    path.mkdir(exist_ok=True, mode=0o700)
    return path


def logs_dir(vault_uuid: str) -> Path:
    return data_dir(vault_uuid) / "logs"


def legacy_waiting() -> bool:
    """Whether old, untagged data is still in the Nuru folder, for a vault to adopt."""
    return any((LEGACY_DB / name).exists() for name in DB_FILES)


class MigrationError(RuntimeError):
    pass


def _state(directory: Path) -> dict:
    try:
        return json.loads((directory / "migration.json").read_text())
    except (OSError, ValueError):
        return {}


def _set_state(directory: Path, **state) -> None:
    path = directory / "migration.json"
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w") as file:
        json.dump(state, file)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def _retire_legacy() -> None:
    """The old files, kept as *.pre-wp04 until the user removes them; idempotent."""
    for name in DB_FILES:
        for suffix in ("", "-wal", "-shm", ".lock"):
            old = LEGACY_DB / (name + suffix)
            if old.exists():
                os.replace(old, old.with_name(old.name + LEGACY_SUFFIX))


def recover(vault_uuid: str) -> None:
    """Finish or undo a move a crash interrupted, so the service never opens a half-moved db/: a
    staging copy with nothing recorded → discarded; staged with db/ not yet replaced → the copy is discarded and nothing changed; staged with db/
    in place → it activated, so it's recorded and finished; active → the old files are retired."""
    directory = data_dir(vault_uuid)
    state = _state(directory).get("state")
    staging = directory / "db.staging"
    if state is None and staging.exists():  # a copy cut off before anything was recorded
        shutil.rmtree(staging)
        return
    if state == "staged" and staging.exists():
        shutil.rmtree(staging)
        aside = directory / "db.replaced"
        if aside.exists() and not (directory / "db").exists():
            os.replace(aside, directory / "db")
        (directory / "migration.json").unlink()
        return
    if state == "staged":
        _set_state(directory, state="active", at=time.time())
        state = "active"
    if state == "active":
        _retire_legacy()


def _checkpoint(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _copy(source: Path, target: Path) -> None:
    src, dst = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def _counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}
    finally:
        conn.close()


def _check(staged: Path, source: Path) -> None:
    conn = sqlite3.connect(f"{staged.as_uri()}?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    if result != "ok":
        raise MigrationError(f"{staged.name}: integrity check failed ({result})")
    if _counts(staged) != _counts(source):
        raise MigrationError(f"{staged.name}: the copy doesn't have the same rows as {source}")


def _check_references(db: Path) -> None:
    """Every conversation the service knows is in the conversation store."""
    service = sqlite3.connect(f"{(db / 'service.db').as_uri()}?mode=ro", uri=True)
    try:
        if not service.execute("SELECT 1 FROM sqlite_master WHERE name='conversations'").fetchone():
            return
        wanted = {row[0] for row in service.execute("SELECT store_id FROM conversations")}
    finally:
        service.close()
    store = sqlite3.connect(f"{(db / 'conversation.db').as_uri()}?mode=ro", uri=True)
    try:
        have = {row[0] for row in store.execute("SELECT id FROM conversations")}
    finally:
        store.close()
    if wanted - have:
        raise MigrationError(f"{len(wanted - have)} of the service's conversations are missing from the conversation store")


def _empty(db: Path) -> bool:
    """A db/ a new-layout service made before the move: no conversations in it yet."""
    for name in DB_FILES:
        path = db / name
        if path.exists() and any(count for table, count in _counts(path).items()
                                 if table in {"conversations", "messages", "turns", "captures"}):
            return False
    return True


def adopt_legacy(vault_uuid: str) -> dict:
    """Move the old, untagged conversations, service state and logs into this vault's folder.
    Run with no service running (the caller checks the old and new service databases' owner locks).
    Returns what was moved; MigrationError (with nothing changed) if it can't be done safely."""
    from core.service.store import ServiceBusy, _lock

    legacy_db, legacy_logs = LEGACY_DB, LEGACY_LOGS  # read now, so tests can point them elsewhere
    directory = data_dir(vault_uuid)
    lock = directory.parent / ".migrate.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise MigrationError(f"another move is under way ({lock}); if none is, remove that file") from None
    os.close(fd)
    held = []
    try:
        recover(vault_uuid)
        sources = [legacy_db / name for name in DB_FILES]
        if not all(path.exists() for path in sources):
            raise MigrationError(f"no old data to move in {legacy_db}")
        for database in (legacy_db / "service.db", db_dir(vault_uuid) / "service.db"):
            try:
                held.append(_lock(database.with_name(database.name + ".lock")))
            except ServiceBusy:
                raise MigrationError("Nuru is running: stop it (quit Obsidian, or Ctrl-C nuru-serve) and run this again") from None
        live = directory / "db"
        if live.exists() and not _empty(live):
            raise MigrationError(f"{live} already holds conversations; moving the old ones in would mix two histories")

        staging = directory / "db.staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(mode=0o700)
        for source in sources:
            _checkpoint(source)
            _copy(source, staging / source.name)
            _check(staging / source.name, source)
        _check_references(staging)

        _set_state(directory, state="staged", at=time.time())
        if live.exists():
            shutil.rmtree(directory / "db.replaced", ignore_errors=True)
            os.replace(live, directory / "db.replaced")
        os.replace(staging, live)  # the one step that puts the move in place
        _set_state(directory, state="active", at=time.time())
        shutil.rmtree(directory / "db.replaced", ignore_errors=True)
        for handle in held:
            handle.close()
        held = []
        _retire_legacy()
        logs = 0
        if legacy_logs.is_dir():  # last: a failure here doesn't undo the move
            target = logs_dir(vault_uuid)
            target.mkdir(parents=True, exist_ok=True)
            for log in legacy_logs.iterdir():
                if log.is_file() and not (target / log.name).exists():
                    shutil.copy2(log, target / log.name)
                    logs += 1
        return {"conversations": _counts(live / "conversation.db").get("conversations", 0), "logs": logs,
                "kept": [str(legacy_db / (name + LEGACY_SUFFIX)) for name in DB_FILES]}
    finally:
        for handle in held:
            handle.close()
        lock.unlink(missing_ok=True)
