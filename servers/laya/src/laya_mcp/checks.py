"""The checks code can decide on its own, run before Laya is ever asked.

gate_write's order is: tool allowlist -> the Obsidian server's permission rules -> path rules ->
note ID validity. The permission rules are mirrored from obsidian_mcp.server (which still enforces
them itself when the write runs); catching them here means a doomed write is refused before it is
proposed, with a reason the harness can show.
"""

import re
from pathlib import PurePosixPath
from typing import Any

from obsidian_mcp.frontmatter_template import TYPE_FOLDER

WRITE_TOOLS = frozenset({"write_note", "rename_or_move_file"})

# obsidian_mcp.server._PROTECTED_FOLDER: the user's own notes. Only write_note (creating one of
# the user/ types with author "user", or adding to a note there) may write there.
PROTECTED_FOLDER = "user"
TEMPLATES_FOLDER = "Templates"
# obsidian_mcp.server._ARCHIVE_FOLDER / _TRASH_FOLDER: archived notes can't be written to, and
# nothing comes back out of trash.
ARCHIVE_FOLDER = "archive"
TRASH_FOLDER = "trash"
PATH_ARGUMENTS = ("source_path", "destination_path", "note")

# A ULID, as create_note mints them: 26 Crockford base32 characters.
NOTE_ID = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b")


def find_note_ids(text: str) -> set[str]:
    return set(NOTE_ID.findall(text))


def _path_problem(value: str) -> str | None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return f"'{value}' is outside the vault"
    if any(part.startswith(".") for part in path.parts):
        return f"'{value}' is inside a hidden/config folder"
    if path.parts and path.parts[0] == TEMPLATES_FOLDER:
        return f"'{value}' is in {TEMPLATES_FOLDER}/, which no tool writes"
    return None


def _id_problem(field: str, value: Any, known_ids: set[str]) -> str | None:
    if not isinstance(value, str) or not NOTE_ID.fullmatch(value):
        return f"{field} {value!r} is not a note ID"
    if value not in known_ids:
        return f"{field} {value} was not returned by any search this session"
    return None


def check_write(tool: str, arguments: dict[str, Any], known_ids: set[str]) -> list[str]:
    """Reasons to block this write; empty if code has no objection."""
    if tool not in WRITE_TOOLS:
        return [f"{tool} is not a write tool the gate allows"]

    reasons = []
    # Permission rules (obsidian_mcp.server): structural tools stay out of user/.
    if tool == "rename_or_move_file":
        for field in ("source_path", "destination_path"):
            value = arguments.get(field)
            if isinstance(value, str) and PurePosixPath(value).parts[:1] == (PROTECTED_FOLDER,):
                reasons.append(f"'{value}' is under {PROTECTED_FOLDER}/, which {tool} may not touch")
        source = arguments.get("source_path")
        if isinstance(source, str) and PurePosixPath(source).parts[:1] == (TRASH_FOLDER,):
            reasons.append(f"'{source}' is in {TRASH_FOLDER}/, and nothing comes back out of trash")
    if tool == "write_note":
        note_type = arguments.get("type")
        if note_type is not None and note_type not in TYPE_FOLDER:
            reasons.append(f"type {note_type!r} is not one of {sorted(TYPE_FOLDER)}")
        elif (note_type and note_type != "daily" and TYPE_FOLDER[note_type] == PROTECTED_FOLDER
              and arguments.get("author") not in (None, "user")):
            reasons.append(f"a {note_type} note is the user's own and needs author 'user'")
        note = arguments.get("note")
        if isinstance(note, str) and PurePosixPath(note).parts[:1] == (ARCHIVE_FOLDER,):
            reasons.append(f"'{note}' is archived; move it out of {ARCHIVE_FOLDER}/ before writing to it")

    # Path rules.
    for field in PATH_ARGUMENTS:
        value = arguments.get(field)
        if isinstance(value, str) and (problem := _path_problem(value)):
            reasons.append(problem)

    # ID validity: every note ID a write names must have come back from a search.
    if tool == "write_note":
        note = arguments.get("note")
        if isinstance(note, str) and NOTE_ID.fullmatch(note) and (problem := _id_problem("note", note, known_ids)):
            reasons.append(problem)
        if arguments.get("supersedes") and (problem := _id_problem("supersedes", arguments["supersedes"], known_ids)):
            reasons.append(problem)
        for target in arguments.get("targets") or []:
            if problem := _id_problem("targets", target, known_ids):
                reasons.append(problem)
    return reasons


def invented_ids(reply: str, known_ids: set[str]) -> list[str]:
    """Note IDs the reply mentions that neither the tool log nor any search result contains."""
    return sorted(find_note_ids(reply) - known_ids)
