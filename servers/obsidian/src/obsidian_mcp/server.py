"""MCP server exposing tools to read an Obsidian vault.

Vault location is read from the VAULT_PATH env var (see .env / .env.example).
Run standalone via stdio with: uv run obsidian-mcp
"""

import os
import re
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

load_dotenv()

VAULT_PATH = Path(os.environ["VAULT_PATH"]).expanduser().resolve()
TAG_PATTERN = re.compile(r"#([A-Za-z0-9_][A-Za-z0-9_/-]*)")

mcp = MCPServer("obsidian-mcp")


def _check_accessible(path: Path) -> None:
    try:
        # rglob() silently skips directories it can't scan (since Python 3.13),
        # so check accessibility explicitly rather than relying on it to raise.
        next(path.iterdir(), None)
    except PermissionError as e:
        raise PermissionError(
            f"Access denied to '{path}'. Check the VAULT_PATH env var and its permissions."
        ) from e


@mcp.tool()
def list_notes() -> list[str]:
    """List all notes (Markdown files) in the vault, as paths relative to the vault root."""
    _check_accessible(VAULT_PATH)
    return sorted(
        str(path.relative_to(VAULT_PATH)) for path in VAULT_PATH.rglob("*.md")
    )


def _is_visible(path: Path) -> bool:
    return not any(part.startswith(".") for part in path.relative_to(VAULT_PATH).parts)


def _is_within_vault(path: Path) -> bool:
    """Check that path, once symlinks are resolved, is actually inside the vault.

    Guards against a symlink stored inside the vault (e.g. VAULT_PATH/leak.md)
    that points outside of it, which a lexical relative_to() check would miss.
    """
    try:
        return path.resolve().is_relative_to(VAULT_PATH)
    except OSError:
        return False


@mcp.tool()
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


@mcp.tool()
def list_files_in_directory(directory: str) -> list[str]:
    """List all files recursively within a subdirectory of the vault, as paths relative to the vault root.

    Args:
        directory: Path to the subdirectory, relative to the vault root. If it
            isn't found directly under the vault root, the vault is searched
            recursively for a directory with that name, and the first match
            (alphabetically, by path) is used.

    Returns an empty list if no matching directory is found anywhere in the vault.
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
            return []
        target = matches[0]

    _check_accessible(target)

    return sorted(
        str(path.relative_to(VAULT_PATH))
        for path in target.rglob("*")
        if path.is_file() and _is_visible(path) and _is_within_vault(path)
    )


@mcp.tool()
def search_notes(query: str) -> list[str]:
    """Search notes (Markdown files) for a string, case-insensitive.

    Args:
        query: The substring to search for within each note's content.

    Returns paths (relative to the vault root) of notes whose content contains
    the query. Returns an empty list if no notes match.
    """
    _check_accessible(VAULT_PATH)
    query_lower = query.lower()
    return sorted(
        str(path.relative_to(VAULT_PATH))
        for path in VAULT_PATH.rglob("*.md")
        if query_lower in path.read_text(errors="ignore").lower()
    )


@mcp.tool()
def list_tags() -> list[str]:
    """List all unique tags (e.g. 'project' for '#project') used across notes in the vault."""
    _check_accessible(VAULT_PATH)
    tags: set[str] = set()
    for path in VAULT_PATH.rglob("*.md"):
        tags.update(TAG_PATTERN.findall(path.read_text(errors="ignore")))
    return sorted(tags)


@mcp.tool()
def count_tags() -> dict[str, int]:
    """Count occurrences of each unique tag (e.g. 'project' for '#project') across notes in the vault."""
    _check_accessible(VAULT_PATH)
    counts: Counter[str] = Counter()
    for path in VAULT_PATH.rglob("*.md"):
        counts.update(TAG_PATTERN.findall(path.read_text(errors="ignore")))
    return dict(sorted(counts.items()))


@mcp.tool()
def read_note(name: str) -> str:
    """Return the full contents of a note (Markdown file) by name.

    Args:
        name: The note's filename, with or without the .md extension. If it
            isn't found directly under the vault root, the vault is searched
            recursively for a note with that name, and the first match
            (alphabetically, by path) is used.

    Returns an empty string if no matching note is found anywhere in the vault.
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
            return ""
        target = matches[0]

    try:
        return target.read_text()
    except PermissionError as e:
        raise PermissionError(
            f"Access denied to '{target}'. Check the VAULT_PATH env var and its permissions."
        ) from e


def main() -> None:
    mcp.run()
