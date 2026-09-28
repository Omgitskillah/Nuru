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

WRITE_TOOLS = frozenset(
    {"add_to_daily", "append_note", "archive_note", "create_note", "generate_templates", "rename_or_move_file"}
)
# Never run for the model, whatever anyone decides: archive_note is the reversible substitute.
BLOCKED_TOOLS = frozenset({"delete_file_or_directory"})

# obsidian_mcp.server._PROTECTED_FOLDER: the user's own notes. Only create_note (for its user/
# types, with author "user"), append_note and add_to_daily may write there.
PROTECTED_FOLDER = "user"
TEMPLATES_FOLDER = "Templates"
PATH_ARGUMENTS = ("source_path", "destination_path", "name")

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
        return f"'{value}' is in {TEMPLATES_FOLDER}/, which only generate_templates writes"
    return None


def _id_problem(field: str, value: Any, known_ids: set[str]) -> str | None:
    if not isinstance(value, str) or not NOTE_ID.fullmatch(value):
        return f"{field} {value!r} is not a note ID"
    if value not in known_ids:
        return f"{field} {value} was not returned by any search this session"
    return None


def check_write(tool: str, arguments: dict[str, Any], known_ids: set[str]) -> list[str]:
    """Reasons to block this write; empty if code has no objection."""
    if tool in BLOCKED_TOOLS:
        return [f"{tool} is blocked; archive the note instead"]
    if tool not in WRITE_TOOLS:
        return [f"{tool} is not a write tool the gate allows"]

    reasons = []
    # Permission rules (obsidian_mcp.server): structural tools stay out of user/.
    if tool in ("rename_or_move_file", "archive_note"):
        for field in PATH_ARGUMENTS:
            value = arguments.get(field)
            if isinstance(value, str) and PurePosixPath(value).parts[:1] == (PROTECTED_FOLDER,):
                reasons.append(f"'{value}' is under {PROTECTED_FOLDER}/, which {tool} may not touch")
    if tool == "create_note":
        note_type = arguments.get("type")
        if note_type not in TYPE_FOLDER:
            reasons.append(f"type {note_type!r} is not one of {sorted(TYPE_FOLDER)}")
        elif TYPE_FOLDER[note_type] == PROTECTED_FOLDER and arguments.get("author") != "user":
            reasons.append(f"a {note_type} note is the user's own and needs author 'user'")

    # Path rules.
    for field in PATH_ARGUMENTS:
        value = arguments.get(field)
        if isinstance(value, str) and (problem := _path_problem(value)):
            reasons.append(problem)
    if tool == "generate_templates" and arguments.get("overwrite"):
        reasons.append("generate_templates with overwrite would replace hand-edited templates")

    # ID validity: every note ID a write names must have come back from a search.
    if tool == "append_note":
        if problem := _id_problem("id", arguments.get("id"), known_ids):
            reasons.append(problem)
    if tool == "create_note":
        if arguments.get("supersedes") and (problem := _id_problem("supersedes", arguments["supersedes"], known_ids)):
            reasons.append(problem)
        for target in arguments.get("targets") or []:
            if problem := _id_problem("targets", target, known_ids):
                reasons.append(problem)
    return reasons


def invented_ids(reply: str, known_ids: set[str]) -> list[str]:
    """Note IDs the reply mentions that neither the tool log nor any search result contains."""
    return sorted(find_note_ids(reply) - known_ids)
