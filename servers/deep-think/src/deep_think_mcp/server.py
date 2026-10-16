"""deep-think: an MCP server whose tools call Claude through the shared claude-access provider
contract (see providers/).

Which provider answers each tool call is picked by the DEEP_THINK_PROVIDER
env var (see providers/__init__.py); right now only "claude" is registered,
and it talks to the real Anthropic API. Run standalone via stdio with:
uv run deep-think-mcp

Its one tool is write_plan, for a harness; ask_an_expert was removed on 2026-10-16 (unused since it
was hidden from the model on 2026-10-05).
"""

import functools
import os
from pathlib import Path
from typing import Callable, TypeVar

from access_logging import AccessLogMiddleware
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from deep_think_mcp.providers import get_provider

LOG_DIR = Path(os.environ.get("DEEP_THINK_MCP_LOG_DIR", Path(__file__).resolve().parents[2] / ".logs"))

# write_plan: the expert as a planner for the harness (nuru's live planner), not a tool a model calls.
# Each planner model at its lowest thinking setting. Haiku 4.5 takes no effort parameter, only a
# thinking budget, and 1024 is the minimum the API accepts (max_tokens must exceed it). Sonnet 5.5
# thinks adaptively and is turned down with effort "low"; it gets the server-side refusal fallback
# ("default"), as recommended for that model.
WRITE_PLAN_MODELS = {
    "claude-haiku-4-5": {"thinking": {"type": "enabled", "budget_tokens": 1024}},
    "claude-sonnet-5-5": {"thinking": {"type": "adaptive"}, "effort": "low", "fallbacks": "default"},
}
WRITE_PLAN_MODEL = "claude-haiku-4-5"
WRITE_PLAN_MAX_TOKENS = 4096

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


@mcp.tool(
    description=f"Have the expert planner ({WRITE_PLAN_MODEL}) write a plan for a request. For a harness, not "
    "a model: request is the user's message verbatim; instructions is the planner's system prompt (how to "
    "plan, the step language, the tools); schema, optional, is a JSON schema to force the plan to; model, "
    "optional, is one of " + ", ".join(WRITE_PLAN_MODELS) + ". Returns {plan, stop_reason, model}: plan is "
    "the planner's text, or empty with a hint."
)
@_tool
def write_plan(request: str, instructions: str, schema: dict | None = None, model: str = WRITE_PLAN_MODEL) -> dict:
    """The planner's reply to `request`, forced to `schema` when one is given (structured outputs - a
    large anyOf schema is refused as "compiled grammar is too large", so the caller may leave it out and
    check the plan itself). Only the text comes back - not the thinking."""
    if not request.strip():
        raise ValueError("request is empty.")
    if model not in WRITE_PLAN_MODELS:
        raise ValueError(f"model must be one of {', '.join(WRITE_PLAN_MODELS)}.")
    settings = WRITE_PLAN_MODELS[model]
    output_config = {**({"effort": settings["effort"]} if "effort" in settings else {}),
                     **({"format": {"type": "json_schema", "schema": schema}} if schema else {})}
    response = get_provider().send_message(
        model=model,
        messages=[{"role": "user", "content": request}],
        system=instructions,
        max_tokens=WRITE_PLAN_MAX_TOKENS,
        thinking=settings["thinking"],
        output_config=output_config or None,
        **({"fallbacks": settings["fallbacks"]} if "fallbacks" in settings else {}),
    )
    plan = "".join(block["text"] for block in response.get("content", []) if block.get("type") == "text")
    # model: who answered - a refusal fallback runs on another model.
    result: dict = {"plan": plan, "stop_reason": response.get("stop_reason"), "model": response.get("model", model)}
    if not plan:
        result["hint"] = "The planner returned no plan text."
    elif response.get("stop_reason") == "max_tokens":
        result["hint"] = "The planner ran out of room, so the plan may be cut off."
    return result


def main() -> None:
    mcp.run()
