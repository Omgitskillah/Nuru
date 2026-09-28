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

LOG_DIR = Path(os.environ.get("DEEP_THINK_MCP_LOG_DIR", Path(__file__).resolve().parents[2] / ".logs"))
DB_PATH = Path(os.environ.get("DEEP_THINK_MCP_DB_PATH", Path(__file__).resolve().parents[2] / ".file_cache.db"))

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


# Sent with every ask_an_expert call. The expert otherwise sees only a message from an
# assistant it knows nothing about, and in practice answered at essay length (7k+ characters,
# ~90 s) to an assistant with a 16k-token window that then had to relay it.
EXPERT_SYSTEM_PROMPT = """You are advising a small local assistant that works in its user's \
Obsidian vault. It will relay your answer to that user. You cannot see the vault or the \
conversation: only the user's request, the attached files (the user's notes), the \
assistant's best guess if it made one, and the assistant's question.

Answer in this order:
1. The answer for the user, in about 250 words or fewer, written so the assistant can pass \
it on as it is.
2. Only if a best guess was given: what in it is wrong or missing, as a short list.

Keep the whole reply short. The assistant has a small context window."""


def _expert_prompt(question: str, best_guess: str, file_paths: list[str], user_request: str) -> str:
    """The labelled sections the expert reads, in the order it needs them."""
    sections = []
    if user_request.strip():
        sections.append(f"## The user's request (verbatim)\n{user_request.strip()}")
    if file_paths:
        sections.append("## Attached files\n" + "\n".join(f"- {path}" for path in file_paths))
    if best_guess.strip():
        sections.append(f"## The assistant's best guess\n{best_guess.strip()}")
    sections.append(f"## The assistant's question\n{question.strip()}")
    return "\n\n".join(sections)


@mcp.tool(
    description=f"Ask the expert model ({ASK_AN_EXPERT_MODEL}) for help, optionally grounded in local "
    "files. question: what you need the expert to decide or check, ending with the question itself. "
    "best_guess: optional - your own draft answer, for the expert to correct. file_paths: optional list "
    "of local file paths to attach (the expert reads them whole, so attach rather than paste). "
    "user_request: the user's original message, verbatim (a client may fill this in itself). "
    "Returns {answer, stop_reason, hint}: answer is written to be passed on to the user; hint says "
    "when it is empty or was cut off."
)
@_tool
def ask_an_expert(
    question: str,
    best_guess: str = "",
    file_paths: list[str] | None = None,
    user_request: str = "",
) -> dict:
    """Ask the expert, and return only what it answered.

    The request is assembled here rather than written freehand by the caller: a small model
    left to write one sent its finished answer with no question in it, or asked the expert
    for notes only the user could supply. So the parts are separate - the user's request
    verbatim, the attached files, the caller's best guess, and the question - and
    EXPERT_SYSTEM_PROMPT tells the expert who it is answering and how briefly. The files go
    first, ahead of the text that refers to them.

    The provider's full response carries the expert's reasoning (thinking blocks, with a long
    signature each) and usage metadata. None of that is the answer, and handed back whole it
    is often larger than the caller's own context window - a 3B local model with 16k tokens
    had its next request rejected outright. So only the text blocks come back, joined, plus
    stop_reason, and a hint when the answer is empty or stopped at max_tokens.
    """
    if not question.strip():
        raise ValueError("question is empty - say what you need the expert to decide or check.")
    provider = get_provider()
    content = [_file_content_block(provider, path) for path in file_paths or []]
    content.append({"type": "text", "text": _expert_prompt(question, best_guess, file_paths or [], user_request)})
    response = provider.send_message(
        model=ASK_AN_EXPERT_MODEL,
        messages=[{"role": "user", "content": content}],
        system=EXPERT_SYSTEM_PROMPT,
        max_tokens=ASK_AN_EXPERT_MAX_TOKENS,
    )
    answer = "\n\n".join(
        block["text"] for block in response.get("content", []) if block.get("type") == "text" and block.get("text")
    )
    stop_reason = response.get("stop_reason")
    result: dict = {"answer": answer, "stop_reason": stop_reason}
    if not answer:
        result["hint"] = (
            "The expert returned no answer text. Do not repeat the same call; tell the user the expert "
            "did not answer, or ask a narrower question."
        )
    elif stop_reason == "max_tokens":
        result["hint"] = "The expert ran out of room before finishing, so the answer may be cut off."
    return result


def main() -> None:
    mcp.run()
