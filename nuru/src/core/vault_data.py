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

import contextlib
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from core.vault_identity import _exclusive, vaults_dir

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


MIGRATE_WAIT_SECONDS = 30  # how long a starting service waits for a move under way


class MigrationBusy(MigrationError):
    """A move is under way: nothing may recover or open this computer's vault data meanwhile."""


def _migration_lock(seconds: float):
    """The lock a move holds from start to finish, and recovery takes before touching anything. An OS
    lock, so a move that crashed doesn't leave it held."""
    return _exclusive(vaults_dir() / ".migrate.lock", seconds, MigrationBusy(
        "Nuru's history is being moved (`nuru-serve vault adopt-legacy`); start Nuru again when it has finished"))


def _copy_logs(vault_uuid: str) -> int:
    """Copy the old logs that aren't in the vault's logs/ yet, each through a .partial file, so a log
    cut off midway is never taken for a whole one and a later pass copies it again. Idempotent."""
    if not LEGACY_LOGS.is_dir():
        return 0
    target = logs_dir(vault_uuid)
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for log in sorted(LEGACY_LOGS.iterdir()):
        if log.is_file() and not (target / log.name).exists():
            partial = target / (log.name + ".partial")
            shutil.copy2(log, partial)
            os.replace(partial, target / log.name)
            copied += 1
    return copied


def _recover(vault_uuid: str) -> int:
    """recover(), for a caller that holds the migration lock. Returns how many logs it copied."""
    directory = data_dir(vault_uuid)
    recorded = _state(directory)
    state = recorded.get("state")
    staging = directory / "db.staging"
    if state is None and staging.exists():  # a copy cut off before anything was recorded
        shutil.rmtree(staging)
        return 0
    if state == "staged" and staging.exists():
        shutil.rmtree(staging)
        aside = directory / "db.replaced"
        if aside.exists() and not (directory / "db").exists():
            os.replace(aside, directory / "db")
        (directory / "migration.json").unlink()
        return 0
    if state == "staged":
        recorded = {"state": "active", "at": time.time()}
        _set_state(directory, **recorded)
        state = "active"
    if state != "active":
        return 0
    shutil.rmtree(directory / "db.replaced", ignore_errors=True)
    _retire_legacy()
    if recorded.get("logs") == "copied":
        return 0
    copied = _copy_logs(vault_uuid)
    _set_state(directory, **{**recorded, "logs": "copied"})
    return copied


def _history_lock(db_path: Path, shared: bool):
    """An open file holding a lock on `<conversation.db>.inuse`: shared for each Nuru session using that
    history, for as long as it runs; exclusive, never waiting, for a move. ServiceBusy if it can't be had.
    Windows has no shared file locks, so there each session locks one byte of its own and a move
    locks them all."""
    from core.service.store import ServiceBusy

    path = db_path.with_name(db_path.name + ".inuse")
    handle = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            import random
            handle.seek(random.randrange(1, 1 << 30) if shared else 0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1 if shared else 1 << 30)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise ServiceBusy(f"a Nuru session is using {db_path}") from None
    return handle


def hold_history(db_path: Path):
    """For a Nuru session (the service's or the CLI's): a shared lock on its conversation database for
    as long as the session runs, so no move replaces it underneath. Close the returned file to let go.
    Take it inside opening(), so a move can't start between recovery and this."""
    return _history_lock(db_path, shared=True)


@contextlib.contextmanager
def opening(vault_uuid: str):
    """For a service starting: recover, then open the databases and take their owner lock inside this
    block, all under the migration lock, so a move can't start between the two (it then sees the
    service's lock and refuses)."""
    with _migration_lock(MIGRATE_WAIT_SECONDS):
        _recover(vault_uuid)
        yield


def recover(vault_uuid: str) -> None:
    """Finish or undo a move a crash interrupted, so the service never opens a half-moved db/. It waits
    for a move under way (MigrationBusy after MIGRATE_WAIT_SECONDS) and never touches one in progress:
    - a staging copy with nothing recorded → discarded;
    - staged with db.staging still there → the rename never happened: the copy is discarded, an empty
      db/ moved aside is put back, and the old files are used as before;
    - staged with db.staging gone → it activated: recorded as active;
    - active → old files not yet retired are retired, and logs not yet copied are copied."""
    with _migration_lock(MIGRATE_WAIT_SECONDS):
        _recover(vault_uuid)


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
    """Move the old, untagged conversations, service state and logs into this vault's folder. Run
    with no service running: it refuses while one holds either service database. Run again after an
    interruption, it finishes what's left. Returns what was moved; MigrationError if it can't be done
    safely, with the old layout still working."""
    from core.service.store import ServiceBusy, _lock

    legacy_db = LEGACY_DB  # read now, so tests can point it elsewhere
    directory = data_dir(vault_uuid)
    with _migration_lock(0):
        finished_logs = _recover(vault_uuid)
        live = directory / "db"
        sources = [legacy_db / name for name in DB_FILES]
        if _state(directory).get("state") == "active" and not any(path.exists() for path in sources):
            return {"conversations": _counts(live / "conversation.db").get("conversations", 0),  # already moved
                    "logs": finished_logs, "kept": [str(legacy_db / (name + LEGACY_SUFFIX)) for name in DB_FILES]}
        if not all(path.exists() for path in sources):
            raise MigrationError(f"no old data to move in {legacy_db}")
        held = []
        try:
            try:
                for database in (legacy_db / "service.db", db_dir(vault_uuid) / "service.db"):
                    held.append(_lock(database.with_name(database.name + ".lock")))
                held.append(_history_lock(db_dir(vault_uuid) / "conversation.db", shared=False))  # no session open
            except ServiceBusy:
                raise MigrationError("Nuru is running: stop it (quit Obsidian, Ctrl-C nuru-serve, close the nuru app) "
                                     "and run this again") from None
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
        finally:
            for handle in held:
                handle.close()
        logs = _recover(vault_uuid)  # active: retire the old files, then copy the logs (resumable)
        return {"conversations": _counts(live / "conversation.db").get("conversations", 0), "logs": logs,
                "kept": [str(legacy_db / (name + LEGACY_SUFFIX)) for name in DB_FILES]}
