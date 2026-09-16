"""SQLite storage for CLI conversations, so chat history can be queried and
later fed back to the model as context.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    title TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    timestamp TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tokens_used INTEGER
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class ConversationStore:
    """One SQLite database of conversations/messages, shared across CLI sessions."""

    def __init__(self, db_path: Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def create_conversation(self, title: str | None = None) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO conversations (created_at, title) VALUES (?, ?)",
                (_now_iso(), title),
            )
        return cursor.lastrowid

    def add_message(self, conversation_id: int, role: str, content: str, tokens_used: int | None = None) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO messages (conversation_id, timestamp, role, content, tokens_used) "
                "VALUES (?, ?, ?, ?, ?)",
                (conversation_id, _now_iso(), role, content, tokens_used),
            )
        return cursor.lastrowid

    def update_tokens(self, message_id: int, tokens_used: int | None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE messages SET tokens_used = ? WHERE id = ?",
                (tokens_used, message_id),
            )

    def get_messages(self, conversation_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, conversation_id, timestamp, role, content, tokens_used "
            "FROM messages WHERE conversation_id = ? ORDER BY timestamp",
            (conversation_id,),
        ).fetchall()
        columns = ["id", "conversation_id", "timestamp", "role", "content", "tokens_used"]
        return [dict(zip(columns, row)) for row in rows]

    def close(self) -> None:
        self._conn.close()
