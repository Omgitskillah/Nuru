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

# Base directory that a relative ask_an_expert file_path is resolved against.
# Callers hand us the paths their own tools returned - nuru passes vault-relative
# ones like "nuru/battery-life-modeling.md" - and this server's working directory
# is its own package, not theirs. Unset keeps the plain relative-to-cwd behaviour.
_base_dir = os.environ.get("DEEP_THINK_BASE_DIR")
BASE_DIR = Path(_base_dir).expanduser().resolve() if _base_dir else None

ASK_AN_EXPERT_MODEL = "claude-opus-5"
# The expert reasons before it answers, and that reasoning is billed against the same
# budget as the reply. The contract's generic 1024 default is smaller than a single
# round of it, so every non-trivial escalation came back stop_reason=max_tokens with a
# thinking block and no text. Large enough here that the answer survives the thinking.
ASK_AN_EXPERT_MAX_TOKENS = 8192

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


def _resolve_attachment(file_path: str) -> str:
    """The absolute path an ask_an_expert file_path refers to.

    An absolute path (or one starting with ~) is taken as given. A relative one is
    resolved against BASE_DIR when it is set, so a caller can pass the paths its own
    tools hand it rather than having to absolutise them first.
    """
    path = Path(file_path).expanduser()
    if path.is_absolute() or BASE_DIR is None:
        return str(path)

    resolved = (BASE_DIR / path).resolve()
    if not resolved.is_file():
        # Name what was tried: a caller that cannot see this server's filesystem
        # otherwise has nothing to correct its next attempt with.
        raise FileNotFoundError(
            f"No such file: '{file_path}' (looked in {BASE_DIR}). "
            "Pass a path relative to that directory, or an absolute path."
        )
    return str(resolved)


def _file_content_block(provider: object, file_path: str) -> dict:
    file_id = resolve_file_id(provider, DB_PATH, _resolve_attachment(file_path))
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
    return provider.send_message(
        model=ASK_AN_EXPERT_MODEL,
        messages=[{"role": "user", "content": content}],
        max_tokens=ASK_AN_EXPERT_MAX_TOKENS,
    )


def main() -> None:
    mcp.run()
