"""SQLite storage for CLI conversations, so chat history can be queried and
later fed back to the model as context.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core.context_budget import estimate_tokens, MESSAGE_FRAMING

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


# Bound metadata scans independently of the budget, even for empty replies
# and legacy conversations containing many orphan rows.
_MAX_HISTORY_EXCHANGES = 100
_MAX_HISTORY_MESSAGES = _MAX_HISTORY_EXCHANGES * 2


class ConversationStore:
    """One SQLite database of conversations/messages, shared across CLI sessions."""

    def __init__(self, db_path: Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # The UI starts the session on one thread and runs each turn on another. Turns never
        # overlap, so sharing the connection between them is safe.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._conn:
            self._conn.executescript(_SCHEMA)
        try:
            self._migrate()
        except Exception:
            self._conn.close()
            raise

    def _migrate(self) -> None:
        """Atomic, versioned backfill, bounded to 256 rows in memory at a time."""
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute("CREATE TABLE IF NOT EXISTS conversation_migrations (version INTEGER PRIMARY KEY)")
            if self._conn.execute("SELECT 1 FROM conversation_migrations WHERE version = 1").fetchone():
                return
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(messages)")}
            if "content_tokens" not in columns:
                self._conn.execute("ALTER TABLE messages ADD COLUMN content_tokens INTEGER")
            last_id = 0
            while rows := self._conn.execute(
                "SELECT id, content FROM messages WHERE id > ? ORDER BY id LIMIT 256", (last_id,)
            ).fetchall():
                self._conn.executemany("UPDATE messages SET content_tokens = ? WHERE id = ?",
                                       [(estimate_tokens(content), mid) for mid, content in rows])
                last_id = rows[-1][0]
            self._conn.execute("INSERT INTO conversation_migrations VALUES (1)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS messages_conversation_id ON messages(conversation_id, id)")

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
                "INSERT INTO messages (conversation_id, timestamp, role, content, tokens_used, content_tokens) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (conversation_id, _now_iso(), role, content, tokens_used, estimate_tokens(content)),
            )
        return cursor.lastrowid

    def update_tokens(self, message_id: int, tokens_used: int | None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE messages SET tokens_used = ? WHERE id = ?",
                (tokens_used, message_id),
            )

    def delete_message(self, message_id: int) -> None:
        """Removes a message outright - used to undo a "user" row whose turn
        never got a response, so it can't linger with tokens_used stuck at
        NULL and throw off get_latest_prompt_tokens for the rest of the
        conversation.
        """
        with self._conn:
            self._conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))

    def get_recent_messages(self, conversation_id: int, token_budget: int, *, before_id: int | None = None) -> list[dict]:
        """Newest complete adjacent user/reply exchanges that fit, in stable ID order.

        Usage is never a message size. Unfinished users and legacy orphan replies
        are skipped; an empty cancelled reply still completes an exchange.
        Fetch content only for selected IDs, after a bounded metadata scan.
        """
        if token_budget <= 0:
            return []
        rows = self._conn.execute(
            "SELECT id, role, content_tokens FROM messages WHERE conversation_id = ? "
            "AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
            (conversation_id, before_id, before_id, _MAX_HISTORY_MESSAGES),
        ).fetchall()
        selected, total, i = [], 0, 0
        while i < len(rows) - 1:
            reply, user = rows[i], rows[i + 1]
            if reply[1] in ("nuru", "assistant") and user[1] == "user":
                cost = reply[2] + user[2] + 2 * MESSAGE_FRAMING
                if total + cost > token_budget:
                    break
                selected.extend((reply[0], user[0]))
                total += cost
                i += 2
            else:
                i += 1
        if not selected:
            return []
        placeholders = ",".join("?" for _ in selected)
        rows = self._conn.execute(
            f"SELECT id, conversation_id, timestamp, role, content, tokens_used, content_tokens "
            f"FROM messages WHERE id IN ({placeholders}) ORDER BY id", selected,
        ).fetchall()
        columns = ["id", "conversation_id", "timestamp", "role", "content", "tokens_used", "content_tokens"]
        return [dict(zip(columns, row)) for row in rows]

    def get_latest_prompt_tokens(self, conversation_id: int) -> int | None:
        """tokens_used of the most recent "user" message in `conversation_id`.

        Ollama's prompt_eval_count (what this is populated from) is already the
        total size of that entire request - full history plus tool schema plus
        the new message - not a per-message marginal cost. So this single most
        recent value, not a sum across messages, is the right proxy for "how
        much of the context window is currently in use". For display purposes
        (e.g. a "% of context used" indicator) - never logged.
        """
        row = self._conn.execute(
            "SELECT tokens_used FROM messages WHERE conversation_id = ? AND role = 'user' "
            "ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        return row[0] if row else None

    def get_messages(self, conversation_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, conversation_id, timestamp, role, content, tokens_used, content_tokens "
            "FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
        columns = ["id", "conversation_id", "timestamp", "role", "content", "tokens_used", "content_tokens"]
        return [dict(zip(columns, row)) for row in rows]

    def close(self) -> None:
        self._conn.close()
