"""nuru-serve's own SQLite database: who owns which conversation, each turn's events and final display
state, and every request ID the service has acted on.

It sits next to the conversation store's database, not inside it. A conversation here names its
conversation-store row (`store_id`), so the two can be joined with ATTACH DATABASE, but SQLite can't
hold a foreign key across files: the service writes the conversation store first, and tolerates this
side being behind.

Callers hold the service's lock: one connection is shared by its HTTP threads and its worker.

One process at a time owns the database: opening it takes an exclusive lock on `<database>.lock`, held
until close(), before migrating or recovering anything. A second nuru-serve on the same database is
refused (ServiceBusy) instead of ending the first one's turns as if it had stopped.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

_MIGRATIONS = {
    1: """
    CREATE TABLE conversations (
        id TEXT PRIMARY KEY,
        principal TEXT NOT NULL,
        store_id INTEGER NOT NULL,  -- conversations.id in the conversation store
        title TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE turns (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        request_id TEXT NOT NULL,
        text TEXT NOT NULL,
        state TEXT NOT NULL,  -- accepted, running, completed, failed, cancelled
        final TEXT,  -- the Display at the terminal event, as JSON
        last_seq INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        ended_at TEXT,
        compacted INTEGER NOT NULL DEFAULT 0  -- events dropped after retention, `final` kept
    );
    CREATE INDEX turns_conversation ON turns(conversation_id);
    CREATE TABLE events (
        turn_id TEXT NOT NULL REFERENCES turns(id),
        seq INTEGER NOT NULL,
        body TEXT NOT NULL,
        PRIMARY KEY (turn_id, seq)
    );
    CREATE TABLE questions (
        id TEXT PRIMARY KEY,
        turn_id TEXT NOT NULL REFERENCES turns(id),
        operation TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        open INTEGER NOT NULL DEFAULT 1
    );
    -- Kept as tombstones for as long as the database lives, so an old ID never becomes new work.
    CREATE TABLE requests (
        key TEXT PRIMARY KEY,  -- protocol.request_key
        operation TEXT NOT NULL,
        request_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        status INTEGER NOT NULL,
        body TEXT NOT NULL,  -- the response given, replayed to an identical retry
        conversation_id TEXT,
        turn_id TEXT,
        created_at TEXT NOT NULL
    );
    """,
    2: """
    CREATE TABLE captures (
        key TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        vault_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        uid TEXT NOT NULL UNIQUE,
        path TEXT NOT NULL UNIQUE,
        note_hash TEXT NOT NULL,
        state TEXT NOT NULL,
        receipt TEXT,
        created_at TEXT NOT NULL,
        completed_at TEXT
    );
    CREATE INDEX captures_device_created ON captures(device_id, created_at);
    """,
    3: """
    ALTER TABLE captures ADD COLUMN legacy INTEGER NOT NULL DEFAULT 0;
    CREATE INDEX captures_device_all ON captures(device_id);
    """,
}
TERMINAL_STATES = ("completed", "failed", "cancelled")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class ServiceBusy(RuntimeError):
    """Another process has the service database open."""


def _lock(path: Path):
    """An open file holding an exclusive lock on `path`, released when the process ends or the file is
    closed; ServiceBusy if someone else holds it."""
    handle = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise ServiceBusy(f"another nuru-serve is using {path.with_suffix('')}") from None
    return handle


class ServiceStore:
    def __init__(self, db_path: Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._owner = _lock(db_path.with_name(db_path.name + ".lock"))
        try:
            self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        except Exception:
            self._owner.close()
            raise
        self._conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._migrate()
        except Exception:
            self.close()
            raise

    def _migrate(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute("CREATE TABLE IF NOT EXISTS service_migrations (version INTEGER PRIMARY KEY)")
            done = {row[0] for row in self._conn.execute("SELECT version FROM service_migrations")}
            for version in sorted(_MIGRATIONS):
                if version not in done:
                    for statement in _MIGRATIONS[version].split(";"):
                        if statement.strip():
                            self._conn.execute(statement)
                    self._conn.execute("INSERT INTO service_migrations VALUES (?)", (version,))
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def transaction(self):
        return _Transaction(self._conn)

    @property
    def in_transaction(self) -> bool:
        return self._conn.in_transaction

    def close(self) -> None:
        self._conn.close()
        if not self._owner.closed:
            if os.name == "nt":
                import msvcrt

                self._owner.seek(0)
                msvcrt.locking(self._owner.fileno(), msvcrt.LK_UNLCK, 1)
            self._owner.close()

    # Capture identities and intents are committed before touching the vault. Rows
    # remain as tombstones after the receipt expires; a reused ID cannot write again.
    def capture(self, key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT device_id, vault_id, request_id, payload_hash, uid, path, note_hash, state, receipt, legacy "
            "FROM captures WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        value = dict(zip(("device_id", "vault_id", "request_id", "payload_hash", "uid", "path",
                          "note_hash", "state", "receipt", "legacy"), row))
        value["receipt"] = json.loads(value["receipt"]) if value["receipt"] else None
        return value

    def add_capture(self, key: str, device_id: str, vault_id: str, request_id: str,
                    payload_hash: str, uid: str, path: str, note_hash: str, *, legacy: bool = False) -> None:
        self._conn.execute(
            "INSERT INTO captures (key, device_id, vault_id, request_id, payload_hash, uid, path, "
            "note_hash, state, created_at, legacy) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?, ?)",
            (key, device_id, vault_id, request_id, payload_hash, uid, path, note_hash, _now(), int(legacy)))

    def set_capture(self, key: str, state: str, receipt: dict | None = None) -> None:
        self._conn.execute(
            "UPDATE captures SET state = ?, receipt = ?, completed_at = CASE WHEN ? = 'completed' "
            "THEN ? ELSE completed_at END WHERE key = ?",
            (state, json.dumps(receipt) if receipt else None, state, _now(), key))

    def pending_captures(self) -> list[str]:
        return [row[0] for row in self._conn.execute(
            "SELECT key FROM captures WHERE state IN ('accepted', 'staging', 'running', 'uncertain')")]

    def capture_count(self, device_id: str | None = None) -> int:
        if device_id is None:
            return self._conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
        return self._conn.execute("SELECT COUNT(*) FROM captures WHERE device_id = ?", (device_id,)).fetchone()[0]

    def delete_completed_legacy(self, key: str | None = None) -> int:
        if key is None:
            return self._conn.execute("DELETE FROM captures WHERE legacy = 1 AND state = 'completed'").rowcount
        return self._conn.execute("DELETE FROM captures WHERE key = ? AND legacy = 1 AND state = 'completed'",
                                  (key,)).rowcount

    def recent_device_captures(self, device_id: str, since: str) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM captures WHERE device_id = ? AND created_at >= ?",
                                  (device_id, since)).fetchone()[0]

    def expire_capture_receipts(self, before: str) -> int:
        cursor = self._conn.execute("UPDATE captures SET state = 'gone', receipt = NULL WHERE state = 'completed' "
                                    "AND completed_at < ?", (before,))
        return cursor.rowcount

    # ------------------------------------------------------------------ requests
    def request(self, key: str) -> dict | None:
        row = self._conn.execute("SELECT operation, request_id, payload_hash, status, body, conversation_id, turn_id "
                                 "FROM requests WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        names = ("operation", "request_id", "payload_hash", "status", "body", "conversation_id", "turn_id")
        found = dict(zip(names, row))
        found["body"] = json.loads(found["body"])
        return found

    def add_request(self, key: str, operation: str, request_id: str, payload_hash: str, status: int, body: dict,
                    conversation_id: str | None = None, turn_id: str | None = None) -> None:
        self._conn.execute(
            "INSERT INTO requests (key, operation, request_id, payload_hash, status, body, conversation_id, turn_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, operation, request_id, payload_hash, status, json.dumps(body), conversation_id, turn_id, _now()))

    # ------------------------------------------------------------------ conversations
    def add_conversation(self, conversation_id: str, principal: str, store_id: int, title: str | None) -> None:
        self._conn.execute("INSERT INTO conversations (id, principal, store_id, title, created_at) VALUES (?, ?, ?, ?, ?)",
                           (conversation_id, principal, store_id, title, _now()))

    def conversation(self, conversation_id: str, principal: str) -> dict | None:
        """The conversation, or None if it doesn't exist or belongs to another principal: the two
        are answered alike, so an ID doesn't give away whether it exists."""
        row = self._conn.execute("SELECT id, store_id, title, created_at FROM conversations WHERE id = ? AND principal = ?",
                                 (conversation_id, principal)).fetchone()
        return dict(zip(("id", "store_id", "title", "created_at"), row)) if row else None

    def conversations(self, principal: str, before: str | None, limit: int) -> list[dict]:
        """Newest first; `before` is the last ID of the previous page."""
        rows = self._conn.execute(
            "SELECT id, title, created_at FROM conversations WHERE principal = ? "
            "AND (? IS NULL OR rowid < (SELECT rowid FROM conversations WHERE id = ?)) ORDER BY rowid DESC LIMIT ?",
            (principal, before, before, limit)).fetchall()
        return [dict(zip(("id", "title", "created_at"), row)) for row in rows]

    # ------------------------------------------------------------------ turns and events
    def add_turn(self, turn_id: str, conversation_id: str, request_id: str, text: str) -> None:
        self._conn.execute("INSERT INTO turns (id, conversation_id, request_id, text, state, created_at) "
                           "VALUES (?, ?, ?, ?, 'accepted', ?)", (turn_id, conversation_id, request_id, text, _now()))

    def turn(self, turn_id: str, principal: str) -> dict | None:
        """The turn, or None if it doesn't exist or another principal owns its conversation."""
        row = self._conn.execute(
            "SELECT t.id, t.conversation_id, t.text, t.state, t.final, t.last_seq, t.compacted FROM turns t "
            "JOIN conversations c ON c.id = t.conversation_id WHERE t.id = ? AND c.principal = ?",
            (turn_id, principal)).fetchone()
        return self._turn(row) if row else None

    def turns(self, conversation_id: str, before: str | None, limit: int) -> list[dict]:
        """A conversation's turns, newest first; `before` is the last ID of the previous page."""
        rows = self._conn.execute(
            "SELECT id, conversation_id, text, state, final, last_seq, compacted FROM turns WHERE conversation_id = ? "
            "AND (? IS NULL OR rowid < (SELECT rowid FROM turns WHERE id = ?)) ORDER BY rowid DESC LIMIT ?",
            (conversation_id, before, before, limit)).fetchall()
        return [self._turn(row) for row in rows]

    def unfinished_turns(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, conversation_id, text, state, final, last_seq, compacted FROM turns "
            f"WHERE state NOT IN {TERMINAL_STATES} ORDER BY rowid").fetchall()
        return [self._turn(row) for row in rows]

    @staticmethod
    def _turn(row) -> dict:
        turn = dict(zip(("id", "conversation_id", "text", "state", "final", "last_seq", "compacted"), row))
        turn["final"] = json.loads(turn["final"]) if turn["final"] else None
        return turn

    def set_turn_state(self, turn_id: str, state: str) -> None:
        self._conn.execute("UPDATE turns SET state = ? WHERE id = ?", (state, turn_id))

    def end_turn(self, turn_id: str, state: str, final: dict) -> None:
        self._conn.execute("UPDATE turns SET state = ?, final = ?, ended_at = ? WHERE id = ?",
                           (state, json.dumps(final), _now(), turn_id))

    def add_event(self, event: dict) -> None:
        self._conn.execute("INSERT INTO events (turn_id, seq, body) VALUES (?, ?, ?)",
                           (event["turn_id"], event["seq"], json.dumps(event, ensure_ascii=False)))
        self._conn.execute("UPDATE turns SET last_seq = ? WHERE id = ?", (event["seq"], event["turn_id"]))

    def events(self, turn_id: str, after: int = 0) -> list[dict]:
        rows = self._conn.execute("SELECT body FROM events WHERE turn_id = ? AND seq > ? ORDER BY seq",
                                  (turn_id, after)).fetchall()
        return [json.loads(body) for (body,) in rows]

    def compact(self, older_than_days: int, now: datetime | None = None) -> int:
        """Drop the events of turns that ended more than `older_than_days` ago; they keep `final`.
        Returns how many turns were compacted."""
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=older_than_days)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        turns = [row[0] for row in self._conn.execute(
            "SELECT id FROM turns WHERE compacted = 0 AND ended_at IS NOT NULL AND ended_at < ?", (cutoff,))]
        for turn_id in turns:
            self._conn.execute("DELETE FROM events WHERE turn_id = ?", (turn_id,))
            self._conn.execute("UPDATE turns SET compacted = 1 WHERE id = ?", (turn_id,))
        return len(turns)

    # ------------------------------------------------------------------ questions
    def add_question(self, question_id: str, turn_id: str, operation: str, expires_at: str) -> None:
        self._conn.execute("INSERT INTO questions (id, turn_id, operation, expires_at) VALUES (?, ?, ?, ?)",
                           (question_id, turn_id, operation, expires_at))

    def question(self, question_id: str, principal: str) -> dict | None:
        row = self._conn.execute(
            "SELECT q.id, q.turn_id, q.operation, q.expires_at, q.open FROM questions q JOIN turns t ON t.id = q.turn_id "
            "JOIN conversations c ON c.id = t.conversation_id WHERE q.id = ? AND c.principal = ?",
            (question_id, principal)).fetchone()
        return dict(zip(("id", "turn_id", "operation", "expires_at", "open"), row)) if row else None

    def close_question(self, question_id: str) -> None:
        self._conn.execute("UPDATE questions SET open = 0 WHERE id = ?", (question_id,))


class _Transaction:
    """One transaction; inside another, it joins the outer one, which commits or rolls back for both."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn, self._outer = conn, False

    def __enter__(self):
        if not self._conn.in_transaction:
            self._conn.execute("BEGIN IMMEDIATE")
            self._outer = True

    def __exit__(self, kind, error, trace):
        if not self._outer:
            return
        if kind is None:
            try:
                self._conn.execute("COMMIT")
                return
            except sqlite3.Error:
                # A failed COMMIT (the database is locked, say) leaves the transaction open: end it here,
                # so nothing after this reads rows that were never committed.
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise
        if self._conn.in_transaction:
            self._conn.execute("ROLLBACK")
