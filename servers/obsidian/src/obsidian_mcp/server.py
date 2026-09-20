"""MCP server exposing tools to read an Obsidian vault.

Vault location is read from the VAULT_PATH env var (see .env / .env.example).
Run standalone via stdio with: uv run obsidian-mcp
"""

import concurrent.futures
import contextlib
import difflib
import functools
import os
import re
import shutil
import sqlite3
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar, cast

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

# archive_note moves a note here, keeping its folder path underneath
# (nuru/x.md -> archive/nuru/x.md): the note's filename never changes, so
# [[links]] to it still resolve, and moving it back is just stripping "archive/".
# browse_notes and the tag tools skip it - an archived note is out of the
# working set - while read_note, search_notes and get_links still reach it.
_ARCHIVE_FOLDER = "archive"

_SLUG_INVALID = re.compile(r"[^a-z0-9]+")

# search_notes returns at most this many hits, best first - a bounded result
# keeps a broad query from flooding a small model's context window. The other
# tools that return a list of notes (notes_with_tag, get_links, all_tags,
# browse_notes) bound theirs the same way, for the same reason.
_SEARCH_LIMIT = 10
_LIST_LIMIT = 25
_BROWSE_MAX_LIMIT = 50
_SNIPPET_CHARS = 120

# Inside one query word, a run of these matches any run of '-', '_' or
# whitespace in a note: "PDP-drop" finds "PDP_drop" and "PDP drop", yet "09-17"
# still finds only that adjacent pair, not every note mentioning 09 and 17
# separately - which is how dates get searched.
_WORD_JOINERS = re.compile(r"[-_]+")
_FRONTMATTER = re.compile(r"\A---\n(?:.*?\n)?---\n", re.DOTALL)

_DATE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
_SESSION_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2})$")

# [[Note]], [[Note|shown as]], [[Note#Heading]], [[Note#^block]] -> "Note".
# Obsidian forbids # ^ [ ] | in a filename, so those end the target. A bare
# [[#Heading]] (a link within the same note) has no target and doesn't match.
_WIKILINK = re.compile(r"\[\[([^\[\]|#^]+?)(?:[#^][^\[\]|]*)?(?:\|[^\[\]]*)?\]\]")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
_CODE_FENCE = re.compile(r"^ {0,3}(```|~~~)")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


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


@mcp.tool(
    description="List notes newest first - the way to get oriented or answer \"what's my latest daily\". "
    f"type: optional filter, one of {', '.join(sorted(template.TYPE_FOLDER))}. limit: how many notes "
    f"(default 10, max {_BROWSE_MAX_LIMIT}). Returns {{count, notes, hint}}: notes are {{path, id, type, "
    "title}}, count is the total that match. Newest = when create_note made it, or last-modified for "
    "notes made in Obsidian; with type daily, transcript or session it is the latest date / session "
    "number instead. Excludes Templates/ and archive/. Pass path to read_note, id to append_note "
    "(id is null for notes not made by create_note)."
)
@_tool
def browse_notes(type: str | None = None, limit: int = 10) -> dict[str, Any]:
    """List notes newest first, optionally only one type.

    Replaces list_notes as the way to get oriented: a bounded, ordered slice
    instead of every path in the vault.

    Args:
        type: Only notes of this type (one of template.TYPE_FOLDER's keys).
            A note create_note made has its type in the index; any other note
            counts as the type its own frontmatter declares, so a note made in
            Obsidian from a Templates/ file is found too. Blank means no filter.
        limit: How many notes to return, at least 1; capped at
            _BROWSE_MAX_LIMIT so this can't turn back into a whole-vault dump.

    Returns {"count", "notes"}: "notes" holds at most `limit` of
    {"path", "id", "type", "title"}, newest first; "count" is how many notes
    match in total. Ordered by `created` for a note in the index, else by the
    file's modified time - except when `type` is daily, transcript or session
    (the types with a `date` or `session` field), which order by that field,
    so "the latest daily" is the latest date even if an earlier day's daily
    was written more recently. A "hint" explains an empty result, or that results
    were cut off - a bare empty list would reach the model as a blank tool
    result it can't interpret.
    Excludes Templates/ and archive/ (see _ARCHIVE_FOLDER).
    """
    _check_accessible(VAULT_PATH)
    wanted = (type or "").strip().lower() or None
    if wanted is not None and wanted not in template.TYPE_FOLDER:
        raise ValueError(f"Unknown type '{type}'. Valid types: {', '.join(sorted(template.TYPE_FOLDER))}.")
    if limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit}.")
    limit = min(limit, _BROWSE_MAX_LIMIT)

    indexed = _indexed_notes()
    newest_first = sorted(_live_notes(), key=lambda p: _newest_key(p, indexed), reverse=True)
    rows = (_note_row(path, indexed) for path in newest_first)
    matches = [row for row in rows if wanted is None or row["type"] == wanted]
    field = template.TYPE_FIELD.get(wanted) if wanted else None
    if field in ("date", "session"):
        # "Latest daily" means the latest *date*, not the daily most recently made -
        # a backfilled one is made today. list.sort is stable, so notes with the
        # same date/session (or none) keep the newest-made-first order from above.
        matches.sort(key=lambda row: _sort_field(VAULT_PATH / row["path"], field), reverse=True)

    result: dict[str, Any] = {"count": len(matches), "notes": matches[:limit]}
    if not matches:
        result["hint"] = (
            f"No notes of type '{wanted}' found. A note made in Obsidian only counts if its "
            f"frontmatter says 'type: {wanted}'. Call browse_notes without a type, or use search_notes."
            if wanted
            else "The vault has no notes yet."
        )
    elif len(matches) > limit:
        hint = f"Showing the newest {limit} of {len(matches)} notes."
        if limit < _BROWSE_MAX_LIMIT:
            hint += " Raise limit to see more."
        if wanted is None:
            hint += " Pass type to narrow."
        result["hint"] = hint
    return result


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


def _rel(path: Path) -> str:
    return str(path.relative_to(VAULT_PATH))


def _is_archived(path: Path) -> bool:
    parts = path.relative_to(VAULT_PATH).parts
    return bool(parts) and parts[0] == _ARCHIVE_FOLDER


def _all_notes() -> list[Path]:
    """Every real note in the vault, sorted by path.

    Skips Templates/ (placeholder skeletons), hidden folders (.obsidian,
    Obsidian's .trash) and any symlink that resolves outside the vault - the
    notes the tools below are allowed to read the contents of and name.
    """
    return sorted(
        p
        for p in VAULT_PATH.rglob("*.md")
        if _is_visible(p) and not _is_in_templates(p) and _is_within_vault(p)
    )


def _live_notes() -> list[Path]:
    """_all_notes() minus archive/ - the working set browse_notes and the tag tools show."""
    return [p for p in _all_notes() if not _is_archived(p)]


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


def _indexed_notes() -> dict[str, dict[str, str]]:
    """id/type/title/created of every note create_note has made, keyed by vault-relative path.

    Empty if the index file doesn't exist yet, rather than opening it: that
    would create it (or fail, on a read-only vault) just to learn nothing is
    indexed.
    """
    if not (VAULT_PATH / _INDEX_DB_NAME).is_file():
        return {}
    with contextlib.closing(_open_index()) as conn:
        return note_index.all_by_path(conn)


def _read_frontmatter(text: str) -> dict[str, Any]:
    """A note's YAML frontmatter as a dict - {} if it has none or it isn't a
    valid YAML mapping. Notes typed in Obsidian can have anything up there
    (a hand-written date PyYAML can't parse raises ValueError, not YAMLError),
    and one bad note must not break a tool that scans the whole vault.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return {}
    try:
        data: Any = yaml.safe_load(match.group(0)[4:-4])  # between the two "---\n" fences
    except (yaml.YAMLError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _date_str(value: object) -> str | None:
    """A frontmatter date as 'YYYY-MM-DD'. create_note quotes its dates, so
    they read back as str, but an unquoted `date: 2026-09-20` typed in
    Obsidian reads back as a date object."""
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return value.strip() if isinstance(value, str) else None


def _note_tags(text: str) -> set[str]:
    """Every tag on a note, lowercased and without the '#'.

    Both the frontmatter `tags` list - the only place create_note puts tags,
    so the #tag regex alone would find none of the notes this server made -
    and inline #tags in the body. Lowercased because Obsidian treats tags
    case-insensitively.
    """
    raw: Any = _read_frontmatter(text).get("tags")
    if isinstance(raw, str):
        raw = re.split(r"[,\s]+", raw)
    items = cast("list[object]", raw) if isinstance(raw, list) else []
    body = _FRONTMATTER.sub("", text, count=1)
    tags = {t.lstrip("#") for t in items if isinstance(t, str)} | set(TAG_PATTERN.findall(body))
    return {t.lower() for t in tags if t}


def _note_row(path: Path, indexed: dict[str, dict[str, str]], text: str | None = None) -> dict[str, Any]:
    """{path, id, type, title} for a note, in the shape search_notes uses.

    A note create_note made gets all four from the index. Any other note has
    no id (append_note can't target it) and its filename as title; its type
    is whatever its own frontmatter says, which is what lets a note made from
    a Templates/ file in Obsidian show up under browse_notes(type=...). Pass
    `text` if the caller already read the note; it's only read here if needed.
    """
    rel = _rel(path)
    info = indexed.get(rel)
    if info:
        return {"path": rel, "id": info["id"], "type": info["type"], "title": info["title"]}
    note_type = _read_frontmatter(path.read_text(errors="ignore") if text is None else text).get("type")
    return {"path": rel, "id": None, "type": note_type if isinstance(note_type, str) else None, "title": path.stem}


def _recency(path: Path, indexed: dict[str, dict[str, str]]) -> str:
    """'YYYY-MM-DDTHH:MM' to order notes by, newest last.

    create_note's own `created` for a note the index knows; the file's
    modified time for the rest (made or last saved in Obsidian, so it has no
    `created` the server can trust). Both are local time in the same format,
    so they sort together.
    """
    info = indexed.get(_rel(path))
    if info:
        return info["created"]
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%dT%H:%M")


def _newest_key(path: Path, indexed: dict[str, dict[str, str]]) -> tuple[str, str, str]:
    """Sort key for newest-first: _recency, then the note's id, then its path.

    `created` only goes to the minute, so notes made in the same minute tie on
    it; a ULID sorts by creation time to the millisecond, which orders them
    the way they were actually made. (A note with no id sorts before one that
    has it, then by path - an arbitrary but stable order.)
    """
    rel = _rel(path)
    return (_recency(path, indexed), indexed.get(rel, {}).get("id", ""), rel)


def _sort_field(path: Path, field: str) -> str:
    """A note's own `date` or `session` frontmatter as a string, "" if it has none."""
    return _date_str(_read_frontmatter(path.read_text(errors="ignore")).get(field)) or ""


def _snippet(text: str, patterns: list[re.Pattern[str]]) -> str:
    """About _SNIPPET_CHARS of a note's body around the earliest query-word hit.

    Frontmatter is skipped (a hit there is a tag or alias, not readable
    context). If no word appears in the body - the note matched on its
    filename or title - this is just the start of the body.
    """
    body = _FRONTMATTER.sub("", text, count=1)
    hits = [m.start() for p in patterns if (m := p.search(body))]
    start = max(0, min(hits) - _SNIPPET_CHARS // 3) if hits else 0
    end = start + _SNIPPET_CHARS
    snippet = " ".join(body[start:end].split())
    return f"{'…' if start else ''}{snippet}{'…' if end < len(body) else ''}"


@mcp.tool(
    description="Search notes by content, title and filename. query: one or more words - every word "
    "must appear, in any order, ignoring case; a word like 'PDP-drop' or '09-17' matches its parts "
    "side by side, joined by '-', '_' or a space. Returns "
    "{query, count, matches, hint}: up to 10 matches, best first, each {path, id, type, title, "
    "snippet}; count is the total. Pass path to read_note and id to append_note (id is null for notes "
    "not made by create_note). hint says how to retry or narrow. Excludes Templates/."
)
@_tool
def search_notes(query: str) -> dict[str, Any]:
    """Search notes (Markdown files) by content, title and filename.

    Args:
        query: One or more whitespace-separated words. Every word must
            appear somewhere in the note's content, title or filename, in any
            order, ignoring case. Inside a word, "-" and "_" also match each
            other and a space, and the parts must sit side by side:
            "PDP-drop" finds "PDP_drop" and "PDP drop", and "09-17" finds
            "2026-09-17" but not a note that only mentions 09 and 17
            separately (written "09 17", those are two words and may be
            anywhere).

    Returns {"query", "count", "matches"}. "matches" holds at most
    _SEARCH_LIMIT hits, best first - notes with more query words in their
    filename or title rank above notes that only match in the body, ties
    broken by path - each {"path", "id", "type", "title", "snippet"}. "count"
    is the total number of matching notes, which can exceed len(matches).
    "id", "type" and "title" come from the index, so they're null (title:
    the filename) for a note create_note didn't make; "snippet" is a short
    window of the body around the first hit. A "hint" explains how to retry
    when nothing matches, or how to narrow when results were cut off - a bare
    empty list would reach the model as a blank tool result it can't
    interpret, and small models fill that gap with invented notes.
    Excludes Templates/ - those are placeholder skeletons, not notes.
    """
    _check_accessible(VAULT_PATH)
    patterns: list[re.Pattern[str]] = []
    for word in query.split():
        parts = [re.escape(part) for part in _WORD_JOINERS.split(word) if part]
        if parts:
            patterns.append(re.compile(r"[-_\s]+".join(parts), re.IGNORECASE))
    if not patterns:
        raise ValueError("query is empty - give at least one word to search for.")
    indexed = _indexed_notes()

    hits: list[tuple[int, dict[str, Any]]] = []
    for path in VAULT_PATH.rglob("*.md"):
        if _is_in_templates(path):
            continue
        rel_path = str(path.relative_to(VAULT_PATH))
        info: dict[str, str] = indexed.get(rel_path, {})
        title = info.get("title", path.stem)
        name = f"{path.stem} {title}"
        text = path.read_text(errors="ignore")
        if not all(p.search(name) or p.search(text) for p in patterns):
            continue
        rank = sum(1 for p in patterns if p.search(name))
        match = {
            "path": rel_path,
            "id": info.get("id"),
            "type": info.get("type"),
            "title": title,
            "snippet": _snippet(text, patterns),
        }
        hits.append((rank, match))

    hits.sort(key=lambda hit: (-hit[0], hit[1]["path"]))
    result: dict[str, Any] = {
        "query": query,
        "count": len(hits),
        "matches": [match for _, match in hits[:_SEARCH_LIMIT]],
    }
    if not hits:
        result["hint"] = (
            f"No note contains all of the words in '{query}'. search_notes matches each word "
            "case-insensitively, in any order: retry with fewer or different words, or a different "
            "format (e.g. '2026-09-17' instead of 'September 17')."
        )
    elif len(hits) > _SEARCH_LIMIT:
        result["hint"] = (
            f"Showing the top {_SEARCH_LIMIT} of {len(hits)} matches - add another word to narrow "
            "the search."
        )
    return result


@mcp.tool(
    description="List every tag in use and how many notes carry it, most-used first - both frontmatter "
    "tags (which create_note writes) and inline #tags. Returns {count, tags: {tag: notes}, hint}. "
    "Pass a tag to notes_with_tag. Excludes Templates/ and archive/."
)
@_tool
def all_tags() -> dict[str, Any]:
    """Every tag in use across the vault's notes, with the number of notes carrying each.

    The tool counterpart of the vault://tags and vault://tags/counts
    resources, which a client that only calls tools (the local model's) can't
    reach. Unlike them it also reads frontmatter `tags`, where create_note puts
    every tag - so it lists the tags of notes the server made - and counts
    notes rather than occurrences, matching what notes_with_tag returns for the
    same tag.

    Returns {"count", "tags"}: "tags" maps tag -> number of notes, most-used
    first (ties alphabetical), at most _LIST_LIMIT of them; "count" is the
    total number of distinct tags. A "hint" explains an empty or cut-off result.
    Tags are lowercased, without the '#'. Excludes Templates/ and archive/.
    """
    _check_accessible(VAULT_PATH)
    counts: Counter[str] = Counter()
    for path in _live_notes():
        counts.update(_note_tags(path.read_text(errors="ignore")))

    most_used = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    result: dict[str, Any] = {"count": len(counts), "tags": dict(most_used[:_LIST_LIMIT])}
    if not counts:
        result["hint"] = "No note has any tags."
    elif len(counts) > _LIST_LIMIT:
        result["hint"] = (
            f"Showing the {_LIST_LIMIT} most-used of {len(counts)} tags. "
            "Pass a tag you expect to notes_with_tag to check it directly."
        )
    return result


@mcp.tool(
    description="List the notes carrying a tag, newest first. tag: with or without the '#', any case; "
    "'project' also matches nested tags like 'project/alpha'. Matches frontmatter tags and inline "
    "#tags. Returns {tag, count, matches, hint}: up to 10 matches {path, id, type, title}; count is the "
    "total. A tag that matches nothing gets a hint with the closest real tags. Excludes Templates/ and archive/."
)
@_tool
def notes_with_tag(tag: str) -> dict[str, Any]:
    """List the notes carrying a tag, newest first (same order as browse_notes).

    Args:
        tag: The tag, with or without a leading '#', in any case. A parent tag
            also matches its nested tags, as in Obsidian: "project" finds
            notes tagged "project/alpha" too.

    Returns {"tag", "count", "matches"}: "matches" holds at most
    _SEARCH_LIMIT of {"path", "id", "type", "title"}; "count" is the total. A
    "hint" says how to narrow when results were cut off, and when nothing
    matches suggests the closest tags actually in use (a typo, a plural) -
    a bare empty list would reach the model as a blank tool result it can't
    interpret. Excludes Templates/ and archive/.
    """
    _check_accessible(VAULT_PATH)
    wanted = tag.strip().lstrip("#").lower()
    if not wanted:
        raise ValueError("tag is empty - give a tag such as 'project'. Call all_tags to see the tags in use.")

    indexed = _indexed_notes()
    in_use: set[str] = set()
    hits: list[tuple[tuple[str, str, str], Path, str]] = []
    for path in _live_notes():
        text = path.read_text(errors="ignore")
        tags = _note_tags(text)
        in_use |= tags
        if any(t == wanted or t.startswith(f"{wanted}/") for t in tags):
            hits.append((_newest_key(path, indexed), path, text))

    hits.sort(key=lambda hit: hit[0], reverse=True)
    result: dict[str, Any] = {
        "tag": wanted,
        "count": len(hits),
        "matches": [_note_row(path, indexed, text) for _, path, text in hits[:_SEARCH_LIMIT]],
    }
    if not hits:
        hint = f"No note is tagged '{wanted}'."
        if close := difflib.get_close_matches(wanted, sorted(in_use), n=3, cutoff=0.6):
            hint += f" Did you mean: {', '.join(close)}?"
        result["hint"] = f"{hint} Call all_tags to see every tag in use."
    elif len(hits) > _SEARCH_LIMIT:
        result["hint"] = f"Showing the newest {_SEARCH_LIMIT} of {len(hits)} notes tagged '{wanted}'."
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


class _NoteNames:
    """Every name a note answers to, so a loosely written one - a title, an id,
    a lowercase slug, a [[wikilink]] target - resolves to the note it means.

    Built once per tool call (one walk of the vault, one frontmatter parse per
    note), so resolving many names, as get_links does, is dict lookups. Names
    are compared as slugs, which makes them case- and punctuation-insensitive.
    Each table maps slug -> notes, in path order, so a tie goes to the first
    path alphabetically, as everywhere else in this module.

    Tried in this order: the note's whole vault-relative path, its filename,
    then its titles/aliases/ids (the index's title and id, and the
    frontmatter's `aliases` and `id` - which is where create_note writes its
    title and id, so a title or an id finds the note).
    """

    def __init__(self) -> None:
        indexed = _indexed_notes()
        self.notes = _all_notes()
        self._by_path: dict[str, list[Path]] = {}
        self._by_stem: dict[str, list[Path]] = {}
        self._by_alias: dict[str, list[Path]] = {}
        for path in self.notes:
            rel = _rel(path)
            info = indexed.get(rel, {})
            frontmatter = _read_frontmatter(path.read_text(errors="ignore"))
            aliases = frontmatter.get("aliases")
            self._add(self._by_path, rel.removesuffix(".md"), path)
            self._add(self._by_stem, path.stem, path)
            for name in (
                info.get("title"),
                info.get("id"),
                frontmatter.get("id"),
                *(aliases if isinstance(aliases, list) else []),
            ):
                self._add(self._by_alias, name, path)

    @staticmethod
    def _add(table: dict[str, list[Path]], name: object, path: Path) -> None:
        # A name with nothing left after slugifying (empty, all emoji) can't be
        # matched on, and would otherwise match every other such name.
        if isinstance(name, str) and (key := _slugify(name)):
            # A note that answers to one name several ways (create_note's id is in
            # the index, the frontmatter `id` and the `aliases`) is still one match.
            paths = table.setdefault(key, [])
            if path not in paths:
                paths.append(path)

    def resolve(self, name: str) -> list[Path]:
        """The notes `name` could mean from the best table that has any - [] if none."""
        stem = name.strip().removesuffix(".md")
        last = Path(stem).name  # a wrong or missing folder in front of the right name still finds it
        for table, key in (
            (self._by_path, _slugify(stem)),
            (self._by_stem, _slugify(last)),
            (self._by_alias, _slugify(last)),
        ):
            if key and key in table:
                return table[key]
        return []

    def closest(self, name: str, n: int = 3) -> list[Path]:
        """Up to n notes whose filename or title looks most like `name`, best first."""
        query = _slugify(Path(name.strip().removesuffix(".md")).name)
        if not query:
            return []
        names = sorted(self._by_stem.keys() | self._by_alias.keys())
        found: list[Path] = []
        # Ask for more keys than notes wanted: one note can own several close keys.
        for key in difflib.get_close_matches(query, names, n=n * 3, cutoff=0.4):
            for path in self._by_stem.get(key, []) + self._by_alias.get(key, []):
                if path not in found:
                    found.append(path)
        return found[:n]


def _resolve_notes(name: str) -> list[Path]:
    """Every note `name` could mean, best match first.

    Exact first, as read_note always did: `name` as a vault-relative path,
    then a note with that filename anywhere in the vault (sorted by path).
    Failing both, forgiving (see _NoteNames): case, slug, title, alias and id.
    More than one is only returned when a name genuinely matches several -
    the same filename in two folders, two notes sharing an alias.

    Raises FileNotFoundError, naming the closest notes, if there is none.
    """
    filename = name if name.endswith(".md") else f"{name}.md"

    target = (VAULT_PATH / filename).resolve()
    if target.is_relative_to(VAULT_PATH) and target.is_file():
        return [target]
    exact = sorted(p for p in VAULT_PATH.rglob("*.md") if p.name == filename and _is_within_vault(p))
    if exact:
        return exact

    names = _NoteNames()
    if found := names.resolve(name):
        return found
    message = f"No note found named '{name}'."
    if close := names.closest(name):
        message += f" Closest matches: {', '.join(_rel(p) for p in close)}."
    raise FileNotFoundError(f"{message} Use browse_notes or search_notes to find the right note.")


def _resolve_note(name: str) -> Path:
    """The first of _resolve_notes(name) - for a tool where a tie doesn't matter."""
    return _resolve_notes(name)[0]


def _extract_section(text: str, section: str, rel_path: str) -> str:
    """The part of a note under one heading: the heading line through to just
    before the next heading of the same or a higher level (so its own
    subheadings stay in). `section` matches a heading's text ignoring case,
    extra whitespace and any leading '#'s. A '#' line inside frontmatter or a
    fenced code block is a comment or a shell prompt, not a heading. If the
    same heading appears twice, the first is used.
    """
    lines = text.splitlines()
    frontmatter = _FRONTMATTER.match(text)
    headings: list[tuple[int, int, str]] = []  # (line, level, title)
    fence: str | None = None
    for i in range(frontmatter.group(0).count("\n") if frontmatter else 0, len(lines)):
        if opening := _CODE_FENCE.match(lines[i]):
            if fence is None:
                fence = opening.group(1)
            elif opening.group(1) == fence:
                fence = None
        elif fence is None and (heading := _HEADING.match(lines[i])):
            headings.append((i, len(heading.group(1)), heading.group(2).strip()))

    wanted = " ".join(section.lstrip("#").split()).casefold()
    for n, (start, level, title) in enumerate(headings):
        if " ".join(title.split()).casefold() == wanted:
            end = next((line for line, lvl, _ in headings[n + 1 :] if lvl <= level), len(lines))
            return "\n".join(lines[start:end]).rstrip()

    listed = ", ".join(f"'{title}'" for _, _, title in headings[:_LIST_LIMIT])
    raise ValueError(
        f"No section '{section}' in '{rel_path}'. "
        + (f"Its headings: {listed}." if headings else "It has no headings - read the whole note.")
    )


@mcp.tool(
    description="Return a note's Markdown content. name: a path, filename, title, alias or id - case and "
    "punctuation don't matter ('nuru mcp' finds Nuru-MCP.md); if nothing matches, the error lists the "
    "closest notes. section: optional heading text (e.g. 'Decisions') to return just that part of a long "
    "note, through to the next heading of the same or higher level; an unknown section lists the note's headings."
)
@_tool
def read_note(name: str, section: str | None = None) -> str:
    """Return the contents of a note (Markdown file) by name - all of it, or one section.

    Args:
        name: Which note. Tried in order: as a vault-relative path, as a
            filename anywhere in the vault (the first match, alphabetically by
            path, if several), then forgivingly - case, slug, title, alias and
            id all match, so "nuru mcp", "Nuru MCP" and "nuru-mcp.md" find
            the same note, and so does its title or its id.
        section: A heading's text, to return only that section (see
            _extract_section). Blank or omitted returns the whole note.

    Raises FileNotFoundError if no matching note is found anywhere in the
    vault - never returns an empty string, which would be indistinguishable
    from a note that's genuinely empty - and the message names the closest
    notes. A hint returned as ordinary text couldn't be told from the note's
    own content, which is why a miss is an error and not a result. Raises
    ValueError, listing the note's headings, if `section` isn't one of them.
    """
    _check_accessible(VAULT_PATH)

    target = _resolve_note(name)

    try:
        text = target.read_text()
    except PermissionError as e:
        raise PermissionError(
            f"Access denied to '{target}'. Check the VAULT_PATH env var and its permissions."
        ) from e
    if section is None or not section.strip():
        return text
    return _extract_section(text, section, _rel(target))


def _link_targets(text: str) -> list[str]:
    """The distinct [[wikilink]] targets in `text`, in order of first appearance.

    Only the note name: the |alias, #heading and #^block parts are dropped, and
    an embed (![[Note]]) counts, as it does in Obsidian's own link graph.
    """
    targets = (m.group(1).strip() for m in _WIKILINK.finditer(text))
    return list(dict.fromkeys(t for t in targets if t))


@mcp.tool(
    description="Show a note's place in the link graph: the notes it links to with [[wikilinks]] (resolved "
    "to paths) and the notes that link to it (backlinks). name: as for read_note. Returns {note, outgoing, "
    "backlinks, hint}: outgoing is [{link, path}] with path null if no note has that name; backlinks is a "
    "list of paths. Follow a path with read_note instead of guessing a search query. Each list shows up "
    "to 25."
)
@_tool
def get_links(name: str) -> dict[str, Any]:
    """A note's outgoing [[wikilinks]] resolved to paths, plus its backlinks.

    Args:
        name: The note, resolved as read_note resolves it.

    Returns {"note", "outgoing", "backlinks"}. "note" is the resolved path.
    "outgoing" holds {"link", "path"}: `link` is the target as written and
    `path` the note it names (resolved as read_note resolves a name, so a link
    by filename, title, alias, id or folder/path all work), or null when no
    note has that name - a broken link, or an attachment. "backlinks" holds the
    paths of the other notes that link here, alphabetically. Each list is
    capped at _LIST_LIMIT, and a "hint" says so, or explains an empty or
    partly-unresolved result. A note's link to itself, and a bare [[#Heading]],
    aren't counted. Archived notes take part; Templates/ and hidden folders don't.
    """
    _check_accessible(VAULT_PATH)
    target = _resolve_note(name)
    target_rel = _rel(target)
    names = _NoteNames()

    outgoing: list[dict[str, str | None]] = []
    for link in _link_targets(target.read_text(errors="ignore")):
        found = names.resolve(link)
        outgoing.append({"link": link, "path": _rel(found[0]) if found else None})

    backlinks: list[str] = []
    for path in names.notes:
        rel = _rel(path)
        if rel == target_rel:
            continue
        for link in _link_targets(path.read_text(errors="ignore")):
            found = names.resolve(link)
            if found and _rel(found[0]) == target_rel:
                backlinks.append(rel)
                break

    result: dict[str, Any] = {
        "note": target_rel,
        "outgoing": outgoing[:_LIST_LIMIT],
        "backlinks": backlinks[:_LIST_LIMIT],
    }
    hints: list[str] = []
    if len(outgoing) > _LIST_LIMIT:
        hints.append(f"Showing {_LIST_LIMIT} of {len(outgoing)} outgoing links.")
    if len(backlinks) > _LIST_LIMIT:
        hints.append(f"Showing {_LIST_LIMIT} of {len(backlinks)} backlinks.")
    if any(link["path"] is None for link in result["outgoing"]):
        hints.append("A link with path null matches no note in the vault (a broken link, or an attachment).")
    if not outgoing and not backlinks:
        hints.append("This note has no [[links]] to other notes, and no note links to it.")
    if hints:
        result["hint"] = " ".join(hints)
    return result


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


def _frontmatters_mentioning(needle: str) -> Iterator[tuple[Path, dict[str, Any]]]:
    """(path, frontmatter) of every note whose text contains `needle`. The
    substring test comes first, so only the few likely matches pay for a YAML parse."""
    for path in _all_notes():
        text = path.read_text(errors="ignore")
        if needle in text:
            yield path, _read_frontmatter(text)


def _next_session(type: str) -> str:
    """Today's next unused session id, 'YYYY-MM-DD-NN', for a note of this type.

    One past the highest NN any note of this type already has today - not the
    first gap - so a session made later never gets a lower number than an
    earlier one. Per type: a transcript and the session note for the same
    conversation share an id, so each type's first note of the day is -01.
    """
    today = _today()
    numbers = [0]
    for _, frontmatter in _frontmatters_mentioning(today):
        session = frontmatter.get("session")
        if (
            frontmatter.get("type") == type
            and isinstance(session, str)
            and (match := _SESSION_PATTERN.match(session))
            and match.group(1) == today
        ):
            numbers.append(int(match.group(2)))
    number = max(numbers) + 1
    if number > 99:
        raise ValueError(f"Today already has 99 '{type}' notes - pass session explicitly.")
    return f"{today}-{number:02d}"


def _find_daily(day: str) -> Path | None:
    """The daily note for `day` ('YYYY-MM-DD'), whoever made it.

    Found by its frontmatter (type daily, that date), not by guessing a
    filename: a daily made in Obsidian, or by create_note under a title like
    "Daily Note", can be named anything.
    """
    for path, frontmatter in _frontmatters_mentioning(day):
        if frontmatter.get("type") == "daily" and _date_str(frontmatter.get("date")) == day:
            return path
    return None


@mcp.tool(
    description=(
        "Create a new note in the vault. type: one of directives, daily, correction, profile, "
        "preferences, person, topic, index, transcript, project, session. title: human-readable "
        "title (becomes the filename). author: one of user, nuru, both (directives/daily/correction "
        "require author=user). body: Markdown content. tags: extra tags, no whitespace. supersedes: "
        "id of a note this replaces, must already exist. date: for type=daily, YYYY-MM-DD, defaults "
        "to today. targets: required for type=correction, list of existing note ids. session: for "
        "type=transcript or type=session, YYYY-MM-DD-NN, defaults to today's next unused number. To "
        "add to today's daily use add_to_daily; to add to any other note use append_note (not this "
        "again). Returns {id, path}."
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
        date: Only valid for type="daily": "YYYY-MM-DD", a real calendar
            date. Defaults to today (see _today) if omitted.
        targets: Required, and only valid, for type="correction". Every id
            in the list must already exist in the index.
        session: Only valid for type="transcript" or type="session":
            "YYYY-MM-DD-NN", with a real calendar date. Defaults to today's
            next unused number for that type (see _next_session) if omitted.

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
    # date and session, unlike targets, are values the server can supply: left
    # out entirely, they default rather than have the caller state (or get
    # wrong) today's date or the next free session number. Only when *no* extra
    # field was passed, so a wrong one (session on a daily) still fails below
    # with an accurate "got [...]", not one polluted by a defaulted value.
    if not type_fields:
        if required_field == "date":
            type_fields["date"] = date = _today()
        elif required_field == "session":
            type_fields["session"] = session = _next_session(type)
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


def _append_text(target: Path, content: str) -> None:
    """Append content to a note's file, starting its own paragraph (see _blank_line_separator)."""

    def _append() -> None:
        separator = _blank_line_separator(target.read_text())
        with target.open("a") as f:
            f.write(separator + content)

    _run_with_timeout(_append)


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

    _append_text(target, content)
    return rel_path


@mcp.tool(
    description="Add text to today's daily note, creating the note first if there isn't one yet. text: "
    "Markdown to add. No date, title or id needed - use this for anything that belongs in today's daily. "
    "Returns {path, id, created}: created says whether this call made the note; id is null for a daily "
    "made in Obsidian rather than by create_note."
)
@_tool
def add_to_daily(text: str) -> dict[str, Any]:
    """Append text to today's daily note, creating it if it doesn't exist.

    Replaces three decisions the model would otherwise make - does today's
    daily exist, what is its id, what is today's date - with none. "Today" is
    the server's clock. An existing daily is found by its frontmatter (see
    _find_daily), so one made in Obsidian is appended to rather than
    duplicated; a new one is a normal create_note daily titled
    "Daily YYYY-MM-DD" (so it's the file daily-YYYY-MM-DD.md), author "user"
    (the only author a daily may have), with `text` as its body.

    Args:
        text: The Markdown to add. Starts its own paragraph, like append_note.

    Returns {"path", "id", "created"}. "id" is null for a note the index
    doesn't know - one made in Obsidian - which append_note couldn't reach.
    """
    _check_accessible(VAULT_PATH)
    if not text.strip():
        raise ValueError("text is empty - give the Markdown to add.")

    today = _today()
    existing = _find_daily(today)
    if existing is None:
        made = create_note(type="daily", title=f"Daily {today}", author="user", body=text, date=today)
        return {"path": made["path"], "id": made["id"], "created": True}

    _append_text(existing, text)
    rel_path = _rel(existing)
    return {"path": rel_path, "id": _indexed_notes().get(rel_path, {}).get("id"), "created": False}


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
    description="Archive a note: move it under archive/, keeping its folder (nuru/x.md -> archive/nuru/x.md), "
    "which takes it out of browse_notes and the tag lists. Reversible - nothing is deleted - so prefer this "
    "to deleting. name: a path, filename, title or id, as for read_note; a name that matches several notes "
    "is refused, so pass the path. Returns {from, to}; move it back with rename_or_move_file(to, from). "
    "Notes under user/ (directives, daily, correction) can't be archived."
)
@_tool
def archive_note(name: str) -> dict[str, str]:
    """Archive a note by moving it to archive/, under its own folder path.

    The safe alternative to deleting: the file, its id and its index row all
    survive (rename_or_move_file repoints the row), and the filename doesn't
    change, so [[links]] to the note keep resolving. Undo it by moving the
    file back - "from" in the result is where it belongs.

    This is a move, so it goes through rename_or_move_file and inherits its
    boundaries: it refuses hidden folders, anything under user/, a destination
    that already exists. Since the name is resolved as read_note resolves it,
    which is forgiving, a name that fits several notes is refused rather than
    guessed - a wrong guess would archive the wrong note.

    Args:
        name: The note to archive, as for read_note.

    Returns {"from", "to"}: vault-relative paths before and after.
    """
    _check_accessible(VAULT_PATH)
    matches = _resolve_notes(name)
    if len(matches) > 1:
        raise ValueError(
            f"'{name}' matches {len(matches)} notes: {', '.join(_rel(p) for p in matches[:5])}. "
            "Pass the path of the one to archive."
        )
    source = _rel(matches[0])
    if _is_archived(matches[0]):
        raise ValueError(f"'{source}' is already archived.")
    destination = f"{_ARCHIVE_FOLDER}/{source}"
    rename_or_move_file(source, destination)
    return {"from": source, "to": destination}


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
