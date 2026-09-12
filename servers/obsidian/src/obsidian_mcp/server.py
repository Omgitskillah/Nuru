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


@mcp.tool()
def list_notes() -> list[str]:
    """List all notes (Markdown files) in the vault, as paths relative to the vault root."""
    try:
        # rglob() silently skips directories it can't scan (since Python 3.13),
        # so check accessibility explicitly rather than relying on it to raise.
        next(VAULT_PATH.iterdir(), None)
    except PermissionError as e:
        raise PermissionError(
            f"Access denied to vault. Check the VAULT_PATH env var and its permissions."
        ) from e

    return sorted(
        str(path.relative_to(VAULT_PATH)) for path in VAULT_PATH.rglob("*.md")
    )


def main() -> None:
    mcp.run()
