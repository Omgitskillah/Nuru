"""Bounded read provenance from explicit tool contracts, never from raw note/model text."""
import json
from itertools import islice

MAX_PER_CALL = 32
MAX_PER_TURN = 128
MAX_PATH = 384
MAX_HEADING = 160
_SEARCHES = frozenset({"search", "search_notes", "semantic_search", "find_in_notes"})
_UNSAFE = frozenset('\\:#^[]|%<>"?*')


def source(path, heading=None) -> dict | None:
    """Portable vault-relative Markdown target; do not normalize an unsafe path into a safe one."""
    if not isinstance(path, str) or not path or len(path) > MAX_PATH or not path.lower().endswith(".md"):
        return None
    if any(c in _UNSAFE or ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in path):
        return None
    if path.split("/")[0].lower() == "trash" or any(not part or part.startswith(".") or part != part.strip() for part in path.split("/")):
        return None
    if heading is not None and (not isinstance(heading, str) or not heading or len(heading) > MAX_HEADING
                                or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in heading)):
        return None
    return {"path": path, **({"heading": heading} if heading is not None else {})}


def merge(existing, incoming, limit=MAX_PER_CALL) -> list[dict]:
    """One target per note, in first-read order; the first read's heading wins."""
    result = []
    seen = set()
    for group in (existing, incoming):
        for item in islice(group, limit):
            if len(result) >= limit:
                return result
            if not isinstance(item, dict):
                continue
            valid = source(item.get("path"), item.get("heading"))
            if valid is not None and valid["path"] not in seen:
                seen.add(valid["path"])
                result.append(valid)
    return result


def result(name: str, arguments: dict, text: str) -> tuple[str, list[dict]]:
    """Only call after a successful read, with arguments restricted to server-advertised contracts.
    Strip only the explicitly requested source envelope;
    revision snapshots retain their existing JSON contract. Links/backlinks aren't read sources."""
    structured = name == "read_note" and (arguments.get("include_source") is True or arguments.get("include_revision") is True)
    if not structured and name != "note_info" and name not in _SEARCHES:
        return text, []
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return text, []
    if not isinstance(data, dict):
        return text, []
    if structured:
        if not isinstance(data.get("content"), str):
            return text, []
        item = data.get("source") if arguments.get("include_source") is True else {"path": data.get("path")}
        sources = merge([], [item])
        # Link eligibility must not change the note body the model receives. A valid path
        # can still link to the note when its heading cannot be represented safely.
        if not sources and isinstance(item, dict):
            sources = merge([], [{"path": item.get("path")}])
        return (text if arguments.get("include_revision") is True else data["content"]), sources
    if name == "note_info":
        return text, merge([], [{"path": data.get("note")}])
    matches = data.get("matches")
    if not isinstance(matches, list):
        return text, []
    items = []
    for match in islice(matches, MAX_PER_CALL):
        if not isinstance(match, dict):
            continue
        if isinstance(match.get("snippet"), str) or isinstance(match.get("text"), str) or isinstance(match.get("lines"), list):
            items.append({"path": match.get("path")})
    return text, merge([], items)
