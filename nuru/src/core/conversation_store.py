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


# Hard cap on how much history get_recent_messages will ever scan/return,
# independent of token_budget - keeps a pathological conversation (or one
# where tokens_used is mostly NULL/0) from forcing an unbounded table scan.
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
            # Added 2026-09-30 for the window's history panel: where a title came from (the model,
            # or a fallback when its summary failed the check) and what the check found.
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(conversations)")}
            for column in ("title_source", "title_check"):
                if column not in columns:
                    self._conn.execute(f"ALTER TABLE conversations ADD COLUMN {column} TEXT")

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

    def delete_message(self, message_id: int) -> None:
        """Removes a message outright - used to undo a "user" row whose turn
        never got a response, so it can't linger with tokens_used stuck at
        NULL and throw off get_latest_prompt_tokens for the rest of the
        conversation.
        """
        with self._conn:
            self._conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))

    def _history_cutoff(self, conversation_id: int, token_budget: int) -> tuple[int | None, int]:
        """Scans only (id, tokens_used) - cheap, no message content - to find the
        oldest id that still fits under token_budget, and the resulting token
        total. Always includes at least the single most recent message, even if
        it alone exceeds the budget. Never looks at more than the most recent
        _MAX_HISTORY_MESSAGES, regardless of budget.

        Returns (cutoff_id, total_tokens); cutoff_id is None if there's no history.
        """
        cursor = self._conn.execute(
            "SELECT id, tokens_used FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (conversation_id, _MAX_HISTORY_MESSAGES),
        )

        cutoff_id = None
        total_tokens = 0
        for message_id, tokens_used in cursor:
            tokens = tokens_used or 0
            if cutoff_id is not None and total_tokens + tokens > token_budget:
                break
            total_tokens += tokens
            cutoff_id = message_id

        return cutoff_id, total_tokens

    def get_recent_messages(self, conversation_id: int, token_budget: int) -> list[dict]:
        """Most recent messages for `conversation_id` whose summed tokens_used fit
        under token_budget, oldest first.

        Two-pass: _history_cutoff finds the oldest id that still fits without
        loading message content; then this fetches full rows for just that
        surviving range, so content is never loaded for discarded history.
        """
        cutoff_id, _ = self._history_cutoff(conversation_id, token_budget)
        if cutoff_id is None:
            return []

        rows = self._conn.execute(
            "SELECT id, conversation_id, timestamp, role, content, tokens_used "
            "FROM messages WHERE conversation_id = ? AND id >= ? ORDER BY id",
            (conversation_id, cutoff_id),
        ).fetchall()
        columns = ["id", "conversation_id", "timestamp", "role", "content", "tokens_used"]
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
            "SELECT id, conversation_id, timestamp, role, content, tokens_used "
            "FROM messages WHERE conversation_id = ? ORDER BY timestamp",
            (conversation_id,),
        ).fetchall()
        columns = ["id", "conversation_id", "timestamp", "role", "content", "tokens_used"]
        return [dict(zip(columns, row)) for row in rows]

    def list_conversations(self, limit: int = 200) -> list[dict]:
        """Conversations that have messages, most recently active first: id, title (None until one
        is set), created_at, last_at (the latest message) and first_prompt (the first user message)."""
        rows = self._conn.execute(
            "SELECT c.id, c.title, c.created_at, MAX(m.timestamp), "
            "(SELECT content FROM messages WHERE conversation_id = c.id AND role = 'user' ORDER BY id LIMIT 1) "
            "FROM conversations c JOIN messages m ON m.conversation_id = c.id "
            "GROUP BY c.id ORDER BY MAX(m.id) DESC LIMIT ?",  # ids always rise; two timestamps can tie
            (limit,),
        ).fetchall()
        columns = ["id", "title", "created_at", "last_at", "first_prompt"]
        return [dict(zip(columns, row)) for row in rows]

    def get_title(self, conversation_id: int) -> str | None:
        row = self._conn.execute("SELECT title FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return row[0] if row else None

    def set_title(self, conversation_id: int, title: str, source: str, check: str | None = None) -> None:
        """`source` is "model" or "fallback"; `check` is what validating the model's summary found (JSON)."""
        with self._conn:
            self._conn.execute(
                "UPDATE conversations SET title = ?, title_source = ?, title_check = ? WHERE id = ?",
                (title, source, check, conversation_id),
            )

    def close(self) -> None:
        self._conn.close()
