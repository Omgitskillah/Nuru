"""laya-mcp: Nuru's decision layer over stdio MCP. No network ports; Laya runs in this process.

Six tools are for Nuru's harness only - it calls them, acts on the answers in code, and never
offers them to the model: assess_effort, tool_gate, gate_write, check_claim, rerank, health. One
is for the model: classify_notes. Nuru builds the model's tool list from an allowlist, so the
split below is documentation for it, not a lock; the harness is the watchdog.

Run standalone via stdio with: uv run laya-mcp
"""

import functools
import os
from pathlib import Path
from typing import Any, Callable, Literal, TypeVar

from access_logging import AccessLogMiddleware
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from laya_mcp.config import load_config
from laya_mcp.decision_log import DecisionLog
from laya_mcp.decisions import DecisionLayer
from laya_mcp.predictor import LayaPredictor
from laya_mcp.question_sets import load_question_sets
from laya_mcp.vault import Vault

load_dotenv(Path(__file__).resolve().parents[4] / ".env.laya")  # optional: VAULT_PATH for classify_notes

LOG_DIR = Path(os.environ.get("LAYA_MCP_LOG_DIR", Path(__file__).resolve().parents[2] / ".logs"))

HARNESS_TOOLS = frozenset({"assess_effort", "tool_gate", "gate_write", "check_claim", "rerank", "watch_thinking", "health"})
MODEL_TOOLS = frozenset({"classify_notes"})

mcp = MCPServer("laya-mcp", middleware=[AccessLogMiddleware(LOG_DIR)])

_layer: DecisionLayer | None = None
_T = TypeVar("_T")


def _get_layer() -> DecisionLayer:
    if _layer is None:
        raise RuntimeError("the decision layer has not been started")
    return _layer


def _tool(fn: Callable[..., _T]) -> Callable[..., _T]:
    """Bad input reaches the caller as a ToolError with its message (see obsidian-mcp's server.py)."""

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> _T:
        try:
            return fn(*args, **kwargs)
        except (ValueError, FileNotFoundError, RuntimeError) as e:
            raise ToolError(f"{type(e).__name__}: {e}") from e

    return wrapper


def _context(session_id: str | None, task_id: str | None, step: int | None) -> dict[str, Any]:
    return {"session_id": session_id, "task_id": task_id, "step": step}


@mcp.tool(description="Harness only. Laya's load state, device, checkpoints and question-set versions.")
@_tool
def health() -> dict[str, Any]:
    layer = _get_layer()
    return {
        "laya": layer.predictor.status(),
        "question_sets": {name: qset.version for name, qset in layer.sets.items()},
        "decision_points": dict(layer.config.decision_points),
        "vault": layer.vault is not None,
    }


@mcp.tool(description="Harness only. Thinking tier for the next step: off, low, high or expert.")
@_tool
def assess_effort(
    request: str,
    step_type: Literal["arguments", "answer"],
    steps: list[dict[str, Any]],
    failures: dict[str, int],
    session_id: str | None = None,
    task_id: str | None = None,
    step: int | None = None,
) -> dict[str, Any]:
    return _get_layer().assess_effort(request, step_type, steps, failures, _context(session_id, task_id, step))


@mcp.tool(
    description="Harness only. phase=start: the request's mode and first tool. phase=next: continue, "
    "answer or ask_user after a tool result. steps: [{tool, status, result}]."
)
@_tool
def tool_gate(
    phase: Literal["start", "next"],
    request: str,
    steps: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    step: int | None = None,
) -> dict[str, Any]:
    layer = _get_layer()
    context = _context(session_id, task_id, step)
    if phase == "start":
        return layer.tool_gate_start(request, context)
    return layer.tool_gate_next(request, steps or [], context)


@mcp.tool(description="Harness only. proceed, ask or block for a proposed vault write, before it runs.")
@_tool
def gate_write(
    request: str,
    tool: str,
    arguments: dict[str, Any],
    known_ids: list[str],
    session_id: str | None = None,
    task_id: str | None = None,
    step: int | None = None,
) -> dict[str, Any]:
    return _get_layer().gate_write(request, tool, arguments, known_ids, _context(session_id, task_id, step))


@mcp.tool(description="Harness only. Checks a drafted reply against the harness's own tool log.")
@_tool
def check_claim(
    reply: str,
    log: list[dict[str, Any]],
    known_ids: list[str],
    session_id: str | None = None,
    task_id: str | None = None,
    step: int | None = None,
) -> dict[str, Any]:
    return _get_layer().check_claim(reply, log, known_ids, _context(session_id, task_id, step))


@mcp.tool(description="Harness only. Mid-reasoning: progressing, repeating, ready or stuck, from the latest thinking.")
@_tool
def watch_thinking(
    request: str,
    thinking: str,
    steps: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    step: int | None = None,
) -> dict[str, Any]:
    return _get_layer().watch_thinking(request, steps or [], thinking, _context(session_id, task_id, step))


@mcp.tool(description="Harness only. Reorders search's matches by meaning by relevance to the query; keeps the top k.")
@_tool
def rerank(
    query: str,
    matches: list[dict[str, Any]],
    session_id: str | None = None,
    task_id: str | None = None,
    step: int | None = None,
) -> dict[str, Any]:
    return _get_layer().rerank(query, matches, _context(session_id, task_id, step))


@mcp.tool(
    description="Label notes by type or subject without reading them. Pick one selector: ids, folder, or "
    "date_from/date_to (YYYY-MM-DD). For subject, pass 2-10 candidate labels."
)
@_tool
def classify_notes(
    question_set: Literal["note_type", "subject"],
    ids: list[str] | None = None,
    folder: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    return _get_layer().classify_notes(question_set, ids, folder, date_from, date_to, labels)


def build_layer() -> DecisionLayer:
    """Load and validate the config and question sets, open the log, and start loading Laya."""
    config = load_config()
    question_sets = load_question_sets()
    predictor = LayaPredictor(config.laya)
    vault_path = os.environ.get("VAULT_PATH")
    layer = DecisionLayer(
        config, question_sets, predictor, DecisionLog(config.database_path), Vault(Path(vault_path)) if vault_path else None
    )
    predictor.start()
    return layer


def main() -> None:
    global _layer
    _layer = build_layer()
    mcp.run()
