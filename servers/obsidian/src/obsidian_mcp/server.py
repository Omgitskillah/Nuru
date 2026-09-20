"""MCP server exposing tools to read an Obsidian vault.

Vault location is read from the VAULT_PATH env var (see .env / .env.example).
Run standalone via stdio with: uv run obsidian-mcp
"""

import concurrent.futures
import contextlib
import functools
import os
import re
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

import yaml
from access_logging import AccessLogMiddleware
from unidecode import unidecode
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from ulid import ULID

from obsidian_mcp import frontmatter_template as template
from obsidian_mcp import index as note_index
from obsidian_mcp import template_bodies

load_dotenv()

VAULT_PATH = Path(os.environ["VAULT_PATH"]).expanduser().resolve()
TAG_PATTERN = re.compile(r"#([A-Za-z0-9_][A-Za-z0-9_/-]*)")

# id -> path/type/title/author/created/supersedes for every note create_note
# has made. Lives inside the vault as a dotfile so list_files/list_notes
# (which skip dotfiles) never surface it.
_INDEX_DB_NAME = ".obsidian-mcp-index.sqlite3"

# generate_templates writes here. Placeholder skeletons, not vault content -
# note-content-scanning tools (list_notes, search_notes, list_tags,
# count_tags) exclude it, or an agent could retrieve "{{title}}" and
# unfilled placeholder text as if it were something a person actually wrote.
_TEMPLATES_FOLDER = "Templates"

_SLUG_INVALID = re.compile(r"[^a-z0-9]+")

_DATE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
_SESSION_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2})$")


def _validate_calendar_date(value: str, field_name: str) -> None:
    """Check value is 'YYYY-MM-DD' and names a real calendar date."""
    if not _DATE_PATTERN.match(value):
        raise ValueError(f"{field_name} must be 'YYYY-MM-DD', got '{value}'.")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"{field_name} '{value}' is not a valid calendar date.") from e


def _validate_session(value: str) -> None:
    """Check value is 'YYYY-MM-DD-NN' with a real calendar date."""
    match = _SESSION_PATTERN.match(value)
    if not match:
        raise ValueError(f"session must be 'YYYY-MM-DD-NN', got '{value}'.")
    _validate_calendar_date(match.group(1), "session")


class _FrontmatterDumper(yaml.SafeDumper):
    """Indents block-list items under their key (`tags:\\n  - x`), which
    PyYAML's default SafeDumper does not do."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)


def _represent_none_as_blank(dumper: yaml.Dumper, _: None) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:null", "")


yaml.add_representer(type(None), _represent_none_as_blank, Dumper=_FrontmatterDumper)


class _DoubleQuoted(str):
    """A string that must render as a double-quoted YAML scalar.

    Used for Obsidian core Templates placeholders like "{{date}}T{{time}}" -
    unquoted, a leading '{' reads as a flow mapping and the frontmatter fails
    to parse. PyYAML would quote it automatically (it must, to stay valid
    YAML) but defaults to single quotes; this forces the double quotes the
    template spec calls for.
    """


def _represent_double_quoted(dumper: yaml.Dumper, data: "_DoubleQuoted") -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


yaml.add_representer(_DoubleQuoted, _represent_double_quoted, Dumper=_FrontmatterDumper)

# Every session's access is logged under here, independent of which client
# connects (Inspector, Claude Desktop, or anything else) - see tools/logging.
LOG_DIR = Path(os.environ.get("OBSIDIAN_MCP_LOG_DIR", Path(__file__).resolve().parents[2] / "logs"))

mcp = MCPServer("obsidian-mcp", middleware=[AccessLogMiddleware(LOG_DIR)])

# Filesystem-mutating tools run their I/O through this so a stalled write
# surfaces as a clear, fast TimeoutError instead of the caller hanging for
# minutes. Small pool: these are local-disk writes, not a workload that needs
# concurrency.
_IO_TIMEOUT_SECONDS = 10.0
_io_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="obsidian-mcp-io")

_T = TypeVar("_T")


def _run_with_timeout(fn: Callable[[], _T]) -> _T:
    """Run fn() with a hard deadline instead of letting it block forever.

    Python has no safe way to forcibly kill a thread stuck in a blocking
    syscall, so on timeout the underlying thread is abandoned rather than
    cancelled - it may still complete (and write to disk) after this raises.
    Callers must use atomic filesystem operations (exclusive create, rename)
    so a late write from an abandoned thread can't silently clobber a
    successful retry.
    """
    future = _io_executor.submit(fn)
    try:
        return future.result(timeout=_IO_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError as e:
        raise TimeoutError(
            f"Vault filesystem operation timed out after {_IO_TIMEOUT_SECONDS:.0f}s. "
            "VAULT_PATH may be on a stalled or unavailable filesystem."
        ) from e


# Every exception type a tool below deliberately raises for an anticipated
# failure (bad input, not found, already exists, permission denied, a timed
# -out write) - never a genuine bug. The MCP framework only forwards a raised
# exception's own message to the client for `ToolError` (or a subclass); any
# other exception is treated as a crash and replaced with a bare
# "Error executing tool <name>", discarding the message and type. @_tool
# converts exactly this set into ToolError, so callers can actually see what
# went wrong; anything NOT in this set (a real bug) still crashes generically,
# which is the right behavior for an unanticipated failure.
_ANTICIPATED_ERRORS = (ValueError, FileNotFoundError, FileExistsError, PermissionError, TimeoutError)


def _tool(fn: Callable[..., _T]) -> Callable[..., _T]:
    """Wrap a tool body so its own ValueError/FileNotFoundError/FileExistsError/
    PermissionError/TimeoutError reach the caller with their message and type
    intact, instead of the framework's generic "Error executing tool <name>".
    """

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> _T:
        try:
            return fn(*args, **kwargs)
        except _ANTICIPATED_ERRORS as e:
            raise ToolError(f"{type(e).__name__}: {e}") from e

    return wrapper


def _check_accessible(path: Path) -> None:
    try:
        # rglob() silently skips directories it can't scan (since Python 3.13),
        # so check accessibility explicitly rather than relying on it to raise.
        next(path.iterdir(), None)
    except PermissionError as e:
        raise PermissionError(
            f"Access denied: '{path}'"
        ) from e


@mcp.tool(description="List all Markdown notes in the vault, as vault-relative paths. Excludes Templates/.")
@_tool
def list_notes() -> list[str]:
    """List all notes (Markdown files) in the vault, as paths relative to the vault root.

    Excludes Templates/ - those are placeholder skeletons, not notes.
    """
    _check_accessible(VAULT_PATH)
    return sorted(
        str(path.relative_to(VAULT_PATH))
        for path in VAULT_PATH.rglob("*.md")
        if not _is_in_templates(path)
    )


def _is_visible(path: Path) -> bool:
    return not any(part.startswith(".") for part in path.relative_to(VAULT_PATH).parts)


def _is_in_templates(path: Path) -> bool:
    parts = path.relative_to(VAULT_PATH).parts
    return bool(parts) and parts[0] == _TEMPLATES_FOLDER


def _is_within_vault(path: Path) -> bool:
    """Check that path, once symlinks are resolved, is actually inside the vault.

    Guards against a symlink stored inside the vault (e.g. VAULT_PATH/leak.md)
    that points outside of it, which a lexical relative_to() check would miss.
    """
    try:
        return path.resolve().is_relative_to(VAULT_PATH)
    except OSError:
        return False


@mcp.tool(
    description="List all files in the vault (notes and attachments), as vault-relative paths. "
    "Excludes .obsidian/ and dotfiles."
)
@_tool
def list_files() -> list[str]:
    """List all files in the vault (notes and attachments alike), as paths relative to the vault root.

    Excludes Obsidian's internal .obsidian/ config folder and dotfiles (e.g. .DS_Store).
    """
    _check_accessible(VAULT_PATH)
    return sorted(
        str(path.relative_to(VAULT_PATH))
        for path in VAULT_PATH.rglob("*")
        if path.is_file() and _is_visible(path)
    )


@mcp.tool(
    description="List all files recursively under a vault subdirectory. directory: subdirectory name or "
    "path relative to the vault root (matched by name anywhere in the vault if not found directly). "
    "Returns {directory, count, files} with files as vault-relative paths; when files is empty, a "
    "'hint' explains why."
)
@_tool
def list_files_in_directory(directory: str) -> dict[str, Any]:
    """List all files recursively within a subdirectory of the vault, as paths relative to the vault root.

    Args:
        directory: Path to the subdirectory, relative to the vault root. If it
            isn't found directly under the vault root, the vault is searched
            recursively for a directory with that name, and the first match
            (alphabetically, by path) is used.

    Returns {"directory", "count", "files"}. When "files" is empty a "hint"
    says whether no matching directory exists anywhere in the vault or the
    directory exists but holds no visible files - a bare empty list would
    reach the model as a blank tool result it can't interpret.
    """
    _check_accessible(VAULT_PATH)

    target = (VAULT_PATH / directory).resolve()
    if not target.is_relative_to(VAULT_PATH) or not target.is_dir():
        name = Path(directory).name
        matches = sorted(
            p
            for p in VAULT_PATH.rglob("*")
            if p.is_dir() and p.name == name and _is_within_vault(p)
        )
        if not matches:
            return {
                "directory": directory,
                "count": 0,
                "files": [],
                "hint": f"No directory matching '{directory}' exists in the vault. "
                "Use list_files to see the real paths.",
            }
        target = matches[0]

    _check_accessible(target)

    files = sorted(
        str(path.relative_to(VAULT_PATH))
        for path in target.rglob("*")
        if path.is_file() and _is_visible(path) and _is_within_vault(path)
    )
    result: dict[str, Any] = {"directory": directory, "count": len(files), "files": files}
    if not files:
        result["hint"] = f"The directory '{directory}' exists but contains no visible files."
    return result


@mcp.tool(
    description="Case-insensitive, literal substring search over note content - search ONE distinctive "
    "word, not a phrase. query: text to search for. Returns {query, count, matches} with matches as "
    "vault-relative paths; when nothing matches, a 'hint' says how to retry. Excludes Templates/."
)
@_tool
def search_notes(query: str) -> dict[str, Any]:
    """Search notes (Markdown files) for a string, case-insensitive.

    Args:
        query: The substring to search for within each note's content.

    Returns {"query", "count", "matches"}, where "matches" are paths (relative
    to the vault root) of notes whose content contains the query. When nothing
    matches a "hint" explains the search is a literal substring match and how
    to retry - a bare empty list would reach the model as a blank tool result
    it can't interpret, and small models fill that gap with invented notes.
    Excludes Templates/ - those are placeholder skeletons, not notes.
    """
    _check_accessible(VAULT_PATH)
    query_lower = query.lower()
    matches = sorted(
        str(path.relative_to(VAULT_PATH))
        for path in VAULT_PATH.rglob("*.md")
        if not _is_in_templates(path) and query_lower in path.read_text(errors="ignore").lower()
    )
    result: dict[str, Any] = {"query": query, "count": len(matches), "matches": matches}
    if not matches:
        result["hint"] = (
            f"No note contains the exact text '{query}'. search_notes is a literal, case-insensitive "
            "substring match: retry with ONE distinctive word or a different format "
            "(e.g. '2026-09-17' instead of 'September 17')."
        )
    return result


@mcp.resource("vault://tags", name="tags", description="All unique tags (e.g. 'project' for '#project') used across notes in the vault.")
def list_tags() -> list[str]:
    """List all unique tags (e.g. 'project' for '#project') used across notes in the vault.

    Excludes Templates/ - those are placeholder skeletons, not notes.
    """
    _check_accessible(VAULT_PATH)
    tags: set[str] = set()
    for path in VAULT_PATH.rglob("*.md"):
        if _is_in_templates(path):
            continue
        tags.update(TAG_PATTERN.findall(path.read_text(errors="ignore")))
    return sorted(tags)


@mcp.resource("vault://tags/counts", name="tag_counts", description="Occurrence counts for each unique tag (e.g. 'project' for '#project') across notes in the vault.")
def count_tags() -> dict[str, int]:
    """Count occurrences of each unique tag (e.g. 'project' for '#project') across notes in the vault.

    Excludes Templates/ - those are placeholder skeletons, not notes.
    """
    _check_accessible(VAULT_PATH)
    counts: Counter[str] = Counter()
    for path in VAULT_PATH.rglob("*.md"):
        if _is_in_templates(path):
            continue
        counts.update(TAG_PATTERN.findall(path.read_text(errors="ignore")))
    return dict(sorted(counts.items()))


@mcp.tool(
    description="Return a note's full Markdown content by filename. name: filename, with or without "
    "the .md extension (searched recursively if not at the vault root). Errors if no note is found."
)
@_tool
def read_note(name: str) -> str:
    """Return the full contents of a note (Markdown file) by name.

    Args:
        name: The note's filename, with or without the .md extension. If it
            isn't found directly under the vault root, the vault is searched
            recursively for a note with that name, and the first match
            (alphabetically, by path) is used.

    Raises FileNotFoundError if no matching note is found anywhere in the
    vault - never returns an empty string, which would be indistinguishable
    from a note that's genuinely empty.
    """
    _check_accessible(VAULT_PATH)

    filename = name if name.endswith(".md") else f"{name}.md"

    target = (VAULT_PATH / filename).resolve()
    if not target.is_relative_to(VAULT_PATH) or not target.is_file():
        matches = sorted(
            p
            for p in VAULT_PATH.rglob("*.md")
            if p.name == filename and _is_within_vault(p)
        )
        if not matches:
            raise FileNotFoundError(f"No note found named '{name}'.")
        target = matches[0]

    try:
        return target.read_text()
    except PermissionError as e:
        raise PermissionError(
            f"Access denied to '{target}'. Check the VAULT_PATH env var and its permissions."
        ) from e


def _resolve_target(path: str) -> Path:
    """Resolve a vault-relative path, rejecting anything that escapes the vault."""
    target = VAULT_PATH / path
    if not _is_within_vault(target):
        raise ValueError(f"Path '{path}' is outside the vault.")
    return target.resolve()


def _open_index() -> sqlite3.Connection:
    """Open the index, reconciled against the current filesystem.

    Reconciling on every open (rather than only "at startup", which this
    long-running process has no other hook for) keeps it correct regardless
    of when or how the vault changed outside this server's own tools.
    """
    conn = note_index.connect(VAULT_PATH / _INDEX_DB_NAME)
    note_index.reconcile(conn, VAULT_PATH)
    return conn


def _slugify(title: str) -> str:
    """Transliterate to ASCII (unidecode), then keep only [a-z0-9], hyphenated.

    A title that's entirely non-Latin (e.g. all CJK, or all emoji) can
    transliterate to nothing - callers must fall back to something else
    (create_note uses the note's own id) rather than treat that as an empty
    title, which it isn't.
    """
    return _SLUG_INVALID.sub("-", unidecode(title).strip().lower()).strip("-")


def _reject_hidden(target: Path, rel_path: str) -> None:
    """Refuse to touch a dotfile/dot-directory (e.g. .obsidian/, this
    server's own index db), the same boundary list_files/list_notes already
    draw for reads. Structural/destructive tools must enforce this, not just
    hide it from a listing - hiding it is cosmetic, this is the actual
    boundary.
    """
    if not _is_visible(target):
        raise PermissionError(f"'{rel_path}' is inside a hidden/config folder (e.g. .obsidian) and is protected.")


# The user/ folder (directives, daily, correction) is meant to be read-only
# to everything except the tools that already gate what goes there by type:
# create_note and append_note. This enforces that for the two structural/
# destructive tools; it is a narrow slice of a real permission model, not one
# - there's no notion here of who's calling, so it can't distinguish a
# human-directed action from an autonomous one.
_PROTECTED_FOLDER = "user"


def _reject_protected_folder(target: Path, rel_path: str) -> None:
    if target.is_relative_to(VAULT_PATH):
        parts = target.relative_to(VAULT_PATH).parts
        if parts and parts[0] == _PROTECTED_FOLDER:
            raise PermissionError(
                f"'{rel_path}' is under '{_PROTECTED_FOLDER}/', which only create_note/append_note may write to."
            )


def _build_frontmatter(
    *,
    note_id: str | None,
    type: str,
    created: str,
    author: str | None,
    supersedes: str | None,
    type_field: tuple[str, object] | None,
    tags: list[str],
    aliases: list[object],
) -> str:
    fields: dict[str, object] = {
        "id": note_id,
        "version": template.VERSION,
        "type": type,
        "created": created,
        "author": author,
        "supersedes": supersedes,
    }
    if type_field is not None:
        fields[type_field[0]] = type_field[1]
    fields["tags"] = tags
    fields["aliases"] = aliases
    return yaml.dump(
        fields, Dumper=_FrontmatterDumper, sort_keys=False, default_flow_style=False, allow_unicode=True
    )


@mcp.tool(
    description=(
        "Create a new note in the vault. type: one of directives, daily, correction, profile, "
        "preferences, person, topic, index, transcript, project, session. title: human-readable "
        "title (becomes the filename). author: one of user, nuru, both (directives/daily/correction "
        "require author=user). body: Markdown content. tags: extra tags, no whitespace. supersedes: "
        "id of a note this replaces, must already exist. date: required for type=daily, format "
        "YYYY-MM-DD. targets: required for type=correction, list of existing note ids. session: "
        "required for type=transcript or type=session, format YYYY-MM-DD-NN. Use append_note (not "
        "this again) to add to a note afterward. Returns {id, path}."
    )
)
@_tool
def create_note(
    type: str,
    title: str,
    author: str,
    body: str = "",
    tags: list[str] | None = None,
    supersedes: str | None = None,
    date: str | None = None,
    targets: list[str] | None = None,
    session: str | None = None,
) -> dict[str, str]:
    """Create a new note, with server-owned frontmatter, in the vault.

    Notes are append-only after creation: frontmatter is written exactly
    once, here, and never rewritten - use append_note to add to the body.

    Args:
        type: One of: directives, daily, correction, profile, preferences,
            person, topic, index, transcript, project, session. Determines
            the note's folder.
        title: Human-readable title. Slugified to become the filename, and
            used verbatim as the first alias.
        author: One of: user, nuru, both. Types under user/ (directives,
            daily, correction) require author="user".
        body: Markdown content, placed under the title heading.
        tags: Extra tags, beyond the type itself (which is always a tag).
            No tag may contain whitespace.
        supersedes: id of the note this one replaces, if any. Must already
            exist in the index.
        date: Required, and only valid, for type="daily": "YYYY-MM-DD",
            a real calendar date.
        targets: Required, and only valid, for type="correction". Every id
            in the list must already exist in the index.
        session: Required, and only valid, for type="transcript" or
            type="session": "YYYY-MM-DD-NN", with a real calendar date.

    Returns {"id": <ulid>, "path": <vault-relative path>}.
    """
    _check_accessible(VAULT_PATH)

    if type not in template.TYPE_FOLDER:
        raise ValueError(f"Unknown type '{type}'. Valid types: {', '.join(sorted(template.TYPE_FOLDER))}.")
    if author not in template.VALID_AUTHORS:
        raise ValueError(f"Unknown author '{author}'. Valid authors: {', '.join(sorted(template.VALID_AUTHORS))}.")
    if template.TYPE_FOLDER[type] == _PROTECTED_FOLDER and author != "user":
        raise ValueError(
            f"type '{type}' is under '{_PROTECTED_FOLDER}/', which only author=\"user\" may create."
        )

    type_fields = {
        name: value
        for name, value in (("date", date), ("targets", targets), ("session", session))
        if value is not None
    }
    required_field = template.TYPE_FIELD.get(type)
    allowed_fields = {required_field} if required_field else set()
    if set(type_fields) != allowed_fields:
        expected = f"'{required_field}'" if required_field else "none"
        raise ValueError(f"type '{type}' expects exactly these extra fields: {expected}; got {sorted(type_fields)}.")
    if date is not None:
        _validate_calendar_date(date, "date")
    if session is not None:
        _validate_session(session)

    if tags:
        invalid_tags = [t for t in tags if re.search(r"\s", t)]
        if invalid_tags:
            raise ValueError(f"tags must not contain whitespace: {invalid_tags}.")

    if not title.strip():
        raise ValueError("title is empty.")

    note_id = str(ULID())
    # A title that's entirely non-Latin (all CJK, all emoji, ...) can
    # transliterate to nothing - fall back to the id rather than reject a
    # non-empty title just because ASCII slugification couldn't represent it.
    slug = _slugify(title) or note_id.lower()

    with contextlib.closing(_open_index()) as conn:
        if supersedes is not None and not note_index.note_exists(conn, supersedes):
            raise ValueError(f"supersedes references unknown note id '{supersedes}'.")
        if targets is not None:
            unknown_targets = [t for t in targets if not note_index.note_exists(conn, t)]
            if unknown_targets:
                raise ValueError(f"targets references unknown note id(s): {unknown_targets}.")

    rel_path = f"{template.TYPE_FOLDER[type]}/{slug}.md"
    target = _resolve_target(rel_path)

    created = datetime.now().strftime("%Y-%m-%dT%H:%M")
    type_field = (required_field, type_fields[required_field]) if required_field else None
    frontmatter = _build_frontmatter(
        note_id=note_id,
        type=type,
        created=created,
        author=author,
        supersedes=supersedes,
        type_field=type_field,
        tags=[type, *(tags or [])],
        aliases=[title, note_id],
    )
    file_content = f"---\n{frontmatter}---\n\n# {title}\n\n{body}"

    def _create() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            # "x" mode opens with O_EXCL: the existence check and the create
            # are one atomic filesystem operation, so two callers racing on
            # the same path (e.g. a client retrying after a timeout while an
            # earlier, abandoned attempt is still landing) can't both succeed.
            with target.open("x") as f:
                f.write(file_content)
        except FileExistsError as e:
            raise FileExistsError(f"A note already exists at '{rel_path}'.") from e

        try:
            with contextlib.closing(_open_index()) as conn:
                note_index.insert_note(
                    conn,
                    id=note_id,
                    path=rel_path,
                    type=type,
                    title=title,
                    author=author,
                    created=created,
                    supersedes=supersedes,
                )
        except sqlite3.IntegrityError as e:
            # A UNIQUE violation here means the index already has a row at
            # this path or id despite reconciliation and the atomic create
            # above - e.g. two callers racing on the same title. Give a
            # message instead of letting a bare sqlite3 error reach the
            # caller as the framework's generic, contentless crash message.
            target.unlink(missing_ok=True)
            raise ValueError(
                f"The index already has an entry for path '{rel_path}' or id '{note_id}'."
            ) from e
        except Exception:
            # The index insert is what makes this note visible to
            # supersedes/append_note lookups - if it fails, the file must
            # not stand, so a retry doesn't collide with a half-created note.
            target.unlink(missing_ok=True)
            raise

    _run_with_timeout(_create)
    return {"id": note_id, "path": rel_path}


def _blank_line_separator(existing: str) -> str:
    """The newlines needed before appended content so it starts its own
    paragraph/block instead of running onto the end of the last line.

    Returns "" for an empty file (nothing to separate from), "\\n" if
    `existing` already ends in exactly one newline (completing the blank
    line), or "\\n\\n" if it ends mid-line or already has a full blank line
    is not yet present.
    """
    if not existing:
        return ""
    if existing.endswith("\n\n"):
        return ""
    if existing.endswith("\n"):
        return "\n"
    return "\n\n"


@mcp.tool(
    description="Append content to an existing note's body. id: the note's id (not its path), as "
    "returned by create_note. content: Markdown to append. Returns the note's vault-relative path."
)
@_tool
def append_note(id: str, content: str) -> str:
    """Append content to an existing note's body, looked up by id.

    A blank line is inserted before `content` if the note doesn't already end
    with one, so the appended text starts its own block instead of running
    onto the end of the note's last line - `content` itself does not need a
    leading newline.

    Args:
        id: The note's id, as returned by create_note (or read/searched, once
            those tools are updated to return ids). Never a path - a typo'd
            id fails loudly instead of silently creating a new note.
        content: The content to append.

    Returns the vault-relative path of the note.
    """
    _check_accessible(VAULT_PATH)

    with contextlib.closing(_open_index()) as conn:
        rel_path = note_index.get_path(conn, id)
    if rel_path is None:
        raise FileNotFoundError(f"No note with id '{id}'.")

    target = _resolve_target(rel_path)
    if not target.is_file():
        raise FileNotFoundError(f"Note '{id}' is indexed at '{rel_path}', but that file no longer exists.")

    def _append() -> None:
        separator = _blank_line_separator(target.read_text())
        with target.open("a") as f:
            f.write(separator + content)

    _run_with_timeout(_append)
    return rel_path


def _build_template_file(type: str) -> str:
    """Build one Obsidian template file's full text for the given type.

    Frontmatter uses the same shape and field order create_note writes for a
    real note, but with placeholder/blank values instead of real ones: id and
    author are left blank (no {{id}}/{{author}} token exists - a human or the
    MCP fills them in later, same as id is blank on a note in progress),
    created uses Obsidian's own {{date}}/{{time}} template tokens, and a
    type's extra frontmatter field (see frontmatter_template.TYPE_FIELD) uses
    {{date}} where a real token exists or is left blank where it doesn't
    (e.g. session's "<YYYY-MM-DD>-NN" has no token for "NN").
    """
    required_field = template.TYPE_FIELD.get(type)
    if required_field == "date":
        type_field = (required_field, _DoubleQuoted("{{date}}"))
    elif required_field == "targets":
        type_field = (required_field, [])
    elif required_field == "session":
        type_field = (required_field, None)
    else:
        type_field = None

    frontmatter = _build_frontmatter(
        note_id=None,
        type=type,
        created=_DoubleQuoted("{{date}}T{{time}}"),
        author=template.TEMPLATE_AUTHOR[type],
        supersedes=None,
        type_field=type_field,
        tags=[type],
        # No id exists yet to be the second alias (create_note mints one only
        # once a real note is created), so this is a single-item list rather
        # than [title, <blank>] - a trailing blank list item parses as a
        # `null` alias in Obsidian, which is worse than just omitting it.
        aliases=[_DoubleQuoted("{{title}}")],
    )
    h1 = f"# {{{{date}}}}" if type == template_bodies.DAILY_TYPE else "# {{title}}"
    body = template_bodies.BODIES[type]
    return f"---\n{frontmatter}---\n\n{h1}\n\n{body}" + ("\n" if body else "")


@mcp.tool(
    description="Regenerate Obsidian template files under Templates/. overwrite: if true, overwrite "
    "existing template files; default false leaves them untouched. Returns {written, skipped} "
    "vault-relative paths."
)
@_tool
def generate_templates(overwrite: bool = False) -> dict[str, list[str]]:
    """Generate the vault's Obsidian template files under Templates/.

    Writes one file per note type (see frontmatter_template.TYPE_FOLDER) to
    the Templates/ folder at the vault root. This is a regeneration of static
    skeleton files for Obsidian's own "Insert template" feature, not a note,
    so it isn't subject to the append-only rule notes are - but a template a
    human has since hand-edited (or that already reflects the current spec)
    still shouldn't be silently clobbered by default.

    Args:
        overwrite: If false (the default), a file that already exists is left
            untouched and reported under "skipped" rather than rewritten. Pass
            true to force every template back to the current generated form.

    Returns {"written": [...], "skipped": [...]} - vault-relative paths.
    """
    _check_accessible(VAULT_PATH)

    written: list[str] = []
    skipped: list[str] = []
    for type, filename in template_bodies.FILENAMES.items():
        rel_path = f"{_TEMPLATES_FOLDER}/{filename}"
        target = _resolve_target(rel_path)

        if not overwrite and target.exists():
            skipped.append(rel_path)
            continue

        content = _build_template_file(type)

        def _write(target: Path = target, content: str = content) -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

        _run_with_timeout(_write)
        written.append(rel_path)

    return {"written": written, "skipped": skipped}


@mcp.tool(
    description="Rename or move a file within the vault. source_path/destination_path: vault-relative "
    "paths. Returns the destination path."
)
@_tool
def rename_or_move_file(source_path: str, destination_path: str) -> str:
    """Rename or move a file within the vault.

    If a note create_note made is being moved, its index row is repointed to
    the new path in the same operation, so it stays reachable by id
    afterward - this is what makes archive-by-move (a move, not a rewrite)
    viable under the append-only design.

    Args:
        source_path: Current path of the file, relative to the vault root.
        destination_path: New path for the file, relative to the vault root.

    Returns the vault-relative destination path.
    """
    _check_accessible(VAULT_PATH)

    source = _resolve_target(source_path)
    destination = _resolve_target(destination_path)
    _reject_hidden(source, source_path)
    _reject_hidden(destination, destination_path)
    _reject_protected_folder(source, source_path)
    _reject_protected_folder(destination, destination_path)

    if not source.is_file():
        raise FileNotFoundError(f"No file found at '{source_path}'.")
    if destination.exists():
        raise FileExistsError(f"A file already exists at '{destination_path}'.")

    source_rel = str(source.relative_to(VAULT_PATH))
    destination_rel = str(destination.relative_to(VAULT_PATH))

    def _move() -> None:
        # Opened (and reconciled) before the physical move: _open_index()
        # reconciles against whatever's on disk right now, and reconciling
        # after the move would see source_rel already gone and delete its
        # row out from under update_path below, rather than repoint it.
        with contextlib.closing(_open_index()) as conn:
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            try:
                note_index.update_path(conn, source_rel, destination_rel)
            except Exception:
                # Keep the index and the filesystem in agreement: if the
                # index update fails, undo the move rather than leave a
                # note's id pointing at wherever it used to be.
                destination.rename(source)
                raise

    _run_with_timeout(_move)
    return destination_rel


@mcp.tool(
    description="Delete a file or directory (recursively) from the vault. path: vault-relative path. "
    "Returns every path deleted."
)
@_tool
def delete_file_or_directory(path: str) -> list[str]:
    """Delete a file or directory (recursively) from the vault.

    Any note create_note made among what's deleted has its index row removed
    too, in the same operation - otherwise its id would keep resolving to a
    path that no longer exists.

    Args:
        path: Path to the file or directory, relative to the vault root.

    Returns the vault-relative paths of everything deleted (the path itself,
    plus every file and subdirectory under it if it was a directory) - so a
    recursive delete's actual extent is visible in the response, not just the
    one path that was passed in.
    """
    _check_accessible(VAULT_PATH)

    target = _resolve_target(path)

    if target == VAULT_PATH:
        raise ValueError("Refusing to delete the vault root.")
    _reject_hidden(target, path)
    _reject_protected_folder(target, path)

    def _delete() -> list[str]:
        if target.is_dir():
            manifest = sorted(str(p.relative_to(VAULT_PATH)) for p in target.rglob("*"))
            manifest.append(str(target.relative_to(VAULT_PATH)))
            shutil.rmtree(target)
            manifest = sorted(manifest)
            with contextlib.closing(_open_index()) as conn:
                for rel in manifest:
                    note_index.delete_by_path(conn, rel)
            return manifest
        elif target.is_file():
            rel = str(target.relative_to(VAULT_PATH))
            target.unlink()
            with contextlib.closing(_open_index()) as conn:
                note_index.delete_by_path(conn, rel)
            return [rel]
        else:
            raise FileNotFoundError(f"No file or directory found at '{path}'.")

    return _run_with_timeout(_delete)


def main() -> None:
    mcp.run()
