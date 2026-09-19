"""deep-think: an MCP server that enforces a single API contract (see
contract.py) across interchangeable backend providers (see providers/).

Which provider answers each tool call is picked by the DEEP_THINK_PROVIDER
env var (see providers/__init__.py); right now only "claude" is registered,
and it talks to the real Anthropic API. Run standalone via stdio with:
uv run deep-think-mcp
"""

import functools
import mimetypes
import os
from pathlib import Path
from typing import Callable, TypeVar

from access_logging import AccessLogMiddleware
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from deep_think_mcp.file_cache import resolve_file_id
from deep_think_mcp.providers import get_provider

LOG_DIR = Path(os.environ.get("DEEP_THINK_MCP_LOG_DIR", Path(__file__).resolve().parents[2] / "logs"))
DB_PATH = Path(os.environ.get("DEEP_THINK_MCP_DB_PATH", Path(__file__).resolve().parents[2] / "file_cache.db"))

ASK_AN_EXPERT_MODEL = "claude-opus-5"

mcp = MCPServer("deep-think-mcp", middleware=[AccessLogMiddleware(LOG_DIR)])

_T = TypeVar("_T")

# Anticipated, deliberately-raised failures (bad input, not found) - see
# obsidian-mcp's server.py for why only these become a ToolError with their
# message intact, rather than the framework's generic "Error executing tool".
_ANTICIPATED_ERRORS = (ValueError, FileNotFoundError)


def _tool(fn: Callable[..., _T]) -> Callable[..., _T]:
    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> _T:
        try:
            return fn(*args, **kwargs)
        except _ANTICIPATED_ERRORS as e:
            raise ToolError(f"{type(e).__name__}: {e}") from e

    return wrapper


def _file_content_block(provider: object, file_path: str) -> dict:
    file_id = resolve_file_id(provider, DB_PATH, file_path)
    mime_type = mimetypes.guess_type(file_path)[0] or ""
    block_type = "image" if mime_type.startswith("image/") else "document"
    return {"type": block_type, "source": {"type": "file", "file_id": file_id}}


@mcp.tool(
    description=f"Ask the expert model ({ASK_AN_EXPERT_MODEL}) a question, optionally grounded in local "
    "files. message: the question to ask. file_paths: optional list of local file paths to attach as "
    "context - each is uploaded (or re-uploaded, if its content changed or its previous upload expired) "
    "and cached by content hash so repeat calls with the same, unchanged file skip the re-upload."
)
@_tool
def ask_an_expert(message: str, file_paths: list[str] | None = None) -> dict:
    provider = get_provider()
    content = [{"type": "text", "text": message}]
    content += [_file_content_block(provider, path) for path in file_paths or []]
    return provider.send_message(model=ASK_AN_EXPERT_MODEL, messages=[{"role": "user", "content": content}])


def main() -> None:
    mcp.run()
