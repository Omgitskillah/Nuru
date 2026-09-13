"""MCP server exposing tools to read an Obsidian vault.

Vault location is read from the VAULT_PATH env var (see .env / .env.example).
Run standalone via stdio with: uv run obsidian-mcp
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

load_dotenv()

VAULT_PATH = Path(os.environ["VAULT_PATH"]).expanduser()

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
        directory: Path to the subdirectory, relative to the vault root.

    Raises FileNotFoundError if the directory does not exist in the vault.
    """
    _check_accessible(VAULT_PATH)

    target = (VAULT_PATH / directory).resolve()
    if not target.is_relative_to(VAULT_PATH.resolve()):
        raise ValueError(f"Directory '{directory}' is outside the vault.")
    if not target.is_dir():
        raise FileNotFoundError(f"Directory '{directory}' does not exist in the vault.")
    _check_accessible(target)

    return sorted(
        str(path.relative_to(VAULT_PATH))
        for path in target.rglob("*")
        if path.is_file() and _is_visible(path)
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


def main() -> None:
    mcp.run()
