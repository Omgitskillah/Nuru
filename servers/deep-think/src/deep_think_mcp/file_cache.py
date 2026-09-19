"""SQLite-backed cache mapping local files to their uploaded Files API file_id.

ask_an_expert (see server.py) calls resolve_file_id() before every request instead of
always re-uploading. A cached entry is reused only if the file's content hash still
matches what was uploaded and its expires_at (as reported by the upload response)
hasn't passed yet - either miss deletes the stale row and triggers a fresh upload.
Files that fall out of the DB (superseded or expired) are never explicitly deleted
from the provider's storage - they're left to auto-expire there.
"""

import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from deep_think_mcp.contract import Provider

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    local_path TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_expires_at ON files (expires_at);
"""


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


def resolve_file_id(provider: Provider, db_path: Path, file_path: str) -> str:
    """Return a live file_id for file_path, (re-)uploading through provider as needed.

    Raises FileNotFoundError if file_path doesn't exist.
    """
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No such file: '{file_path}'")

    content_hash = _hash_file(path)
    now = datetime.now(timezone.utc)

    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT file_id, content_hash, expires_at FROM files WHERE local_path = ?",
            (str(path),),
        ).fetchone()

        if row is not None:
            file_id, cached_hash, expires_at = row
            expired = expires_at is not None and datetime.fromisoformat(expires_at) <= now
            if cached_hash == content_hash and not expired:
                return file_id
            conn.execute("DELETE FROM files WHERE local_path = ?", (str(path),))
            conn.commit()

        uploaded = provider.upload_file(str(path))
        conn.execute(
            "INSERT INTO files (local_path, file_id, content_hash, size_bytes, uploaded_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(path),
                uploaded["id"],
                content_hash,
                uploaded.get("size_bytes", path.stat().st_size),
                now.isoformat(),
                uploaded.get("expires_at"),
            ),
        )
        conn.commit()
        return uploaded["id"]
