"""Read-only access to the vault, for classify_notes.

Files are only ever opened for reading. The Obsidian server's read code isn't reused because
importing obsidian_mcp.server builds that MCP server and requires its env; this keeps to the same
working set instead - no Templates/, no hidden folders, no archive/, no symlinks out of the vault.
Nothing here assumes any folder name beyond those, so it works on any vault layout.
"""

import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath

import yaml

_SKIPPED_TOP = frozenset({"Templates", "archive"})


@dataclass(frozen=True)
class Note:
    path: str  # vault-relative
    id: str | None
    created: date
    body: str  # without frontmatter

    @property
    def key(self) -> str:
        return self.id or self.path


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            try:
                meta = yaml.safe_load(text[4:end]) or {}
            except yaml.YAMLError:
                meta = {}
            return (meta if isinstance(meta, dict) else {}), text[end + 5 :]
    return {}, text


def _created(meta: dict, path: Path) -> date:
    value = meta.get("created")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime).date()


class Vault:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()

    def _in_working_set(self, path: Path) -> bool:
        rel = path.relative_to(self.root)
        if rel.parts[0] in _SKIPPED_TOP or any(part.startswith(".") for part in rel.parts):
            return False
        return path.resolve().is_relative_to(self.root)

    def _read(self, path: Path) -> Note:
        with open(path, encoding="utf-8", errors="replace") as f:
            meta, body = _split_frontmatter(f.read())
        note_id = meta.get("id")
        return Note(
            path=path.relative_to(self.root).as_posix(),
            id=str(note_id) if note_id else None,
            created=_created(meta, path),
            body=body.strip(),
        )

    def notes(self, folder: str | None = None) -> list[Note]:
        base = self.root
        if folder:
            rel = PurePosixPath(folder.strip("/"))
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"folder '{folder}' is outside the vault")
            base = self.root / rel
            if not base.is_dir():
                raise FileNotFoundError(f"no folder '{folder}' in the vault")
        found = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if name.endswith(".md") and self._in_working_set(path):
                    found.append(self._read(path))
        return found

    def select(
        self,
        ids: list[str] | None = None,
        folder: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> tuple[list[Note], list[str]]:
        """Notes matching exactly one kind of selector, and any requested IDs that weren't found."""
        kinds = [bool(ids), bool(folder), bool(date_from or date_to)]
        if sum(kinds) != 1:
            raise ValueError("give exactly one selector: ids, folder, or a date range (date_from/date_to)")
        if ids:
            wanted = set(ids)
            notes = [n for n in self.notes() if n.id in wanted]
            return notes, sorted(wanted - {n.id for n in notes})
        if folder:
            return self.notes(folder), []
        start = date.fromisoformat(date_from) if date_from else date.min
        end = date.fromisoformat(date_to) if date_to else date.max
        return [n for n in self.notes() if start <= n.created <= end], []
