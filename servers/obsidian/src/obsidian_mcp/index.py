"""SQLite index of notes created via create_note: id -> path/type/title/author/created/supersedes.

The vault's Markdown files remain the source of truth for content and
frontmatter; this index exists only so create_note can validate `supersedes`
and so ID-based tools (e.g. append_note) can resolve an id to a path without
scanning the vault. It only knows about notes created through create_note -
a note added any other way (e.g. directly in Obsidian) has no row here.
"""

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    created TEXT NOT NULL,
    supersedes TEXT
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the index, creating its schema if this is a fresh database."""
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def insert_note(
    conn: sqlite3.Connection,
    *,
    id: str,
    path: str,
    type: str,
    title: str,
    author: str,
    created: str,
    supersedes: str | None,
) -> None:
    conn.execute(
        "INSERT INTO notes (id, path, type, title, author, created, supersedes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (id, path, type, title, author, created, supersedes),
    )
    conn.commit()


def note_exists(conn: sqlite3.Connection, note_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM notes WHERE id = ?", (note_id,)).fetchone()
    return row is not None


def get_path(conn: sqlite3.Connection, note_id: str) -> str | None:
    row = conn.execute("SELECT path FROM notes WHERE id = ?", (note_id,)).fetchone()
    return row[0] if row else None
