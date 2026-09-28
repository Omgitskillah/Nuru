"""Shadow mode for the decision layer: at every decision point, ask Laya what it would do, and log
that next to what Nuru actually did. Nothing here changes a turn.

The session calls these hooks as a turn runs:

    begin(request, task_id)        tool_gate (start) and assess_effort, before the first model call
    model_called(tools, ...)       what the model did - resolves the pending decisions' action_taken
    before_tool / after_tool       gate_write for a write; the execution log; rerank for semantic_search
    before_next_call()             tool_gate (next) and assess_effort, after a round of tool results
    end(reply)                     check_claim on the reply the user is shown

Every call to the decision layer goes through `_ask`, which swallows any failure - Laya down, the
server gone, a bad answer - so a shadow problem can never break or change a turn. The execution log
(`tool_calls`) is written from what the harness ran, never from what the model said.
"""

import json
import re
from collections.abc import Callable
from typing import Any

from laya_mcp.checks import BLOCKED_TOOLS, WRITE_TOOLS, find_note_ids
from laya_mcp.decision_log import DecisionLog

from core.providers.mcp_tools import ToolResult

# The tool_gate/assess_effort state carries only this many recent steps (tool name and status),
# plus how many came before - a bounded state, rather than one that grows until Laya's budget
# rejects every decision in a long turn.
_RECENT_STEPS = 6
# A tool's first sentence is its option text in tool_gate; Laya reads ~9 tokens of each anyway.
_FIRST_SENTENCE = re.compile(r"^(.+?[.!?])(\s|$)", re.DOTALL)


def think_tier(think: bool | str) -> str:
    """The tier name for an Ollama `think` value (True is the model's own default, full thinking)."""
    if think is False:
        return "off"
    if think is True:
        return "high"
    return str(think)


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    match = _FIRST_SENTENCE.match(text)
    return (match.group(1) if match else text)[:120]


class DecisionShadow:
    def __init__(
        self,
        call: Callable[[str, dict], ToolResult],
        log: DecisionLog,
        session_id: str,
        model_tools: list[dict],
    ):
        self._call = call
        self._log = log
        self._session_id = session_id
        self._tools = [
            {
                "name": t["function"]["name"],
                "description": _first_sentence(t["function"].get("description", "")),
                "kind": "write" if t["function"]["name"] in WRITE_TOOLS else "read",
            }
            for t in model_tools
        ]
        self.known_ids: set[str] = set()  # every note ID a tool result has shown, this session
        self.failures = 0  # decision-layer calls that failed; the turns went on regardless
        self._reset("", None)

    def _reset(self, request: str, task_id: str | None) -> None:
        self._request = request
        self._task_id = task_id
        self._step = 0
        self._steps: list[dict[str, str]] = []
        self._counts = {"tool_errors": 0, "empty_replies": 0}
        self._executed: list[dict[str, Any]] = []
        self._pending: list[tuple[str, int]] = []  # (kind, decision_id) awaiting what the model did

    # ------------------------------------------------------------------ plumbing
    def _context(self) -> dict[str, Any]:
        return {"session_id": self._session_id, "task_id": self._task_id, "step": self._step}

    def _ask(self, tool: str, arguments: dict) -> dict | None:
        """One decision-layer call; None if it failed in any way."""
        try:
            result = self._call(tool, {**arguments, **self._context()})
            if result.is_error:
                raise RuntimeError(result.text)
            return json.loads(result.text)
        except Exception:
            self.failures += 1
            return None

    def _safely(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            self.failures += 1

    def _recent_steps(self) -> list[dict[str, Any]]:
        earlier = len(self._steps) - _RECENT_STEPS
        recent: list[dict[str, Any]] = list(self._steps[-_RECENT_STEPS:])
        return ([{"earlier_steps": earlier}] if earlier > 0 else []) + recent

    def _gate_then_effort(self, phase: str) -> None:
        gate = self._ask(
            "tool_gate",
            {"phase": phase, "request": self._request, "tools": self._tools, "steps": self._recent_steps()},
        )
        if gate:
            self._pending.append((phase, gate["decision_id"]))
        answering = gate is not None and (gate.get("mode") == "answer" or gate.get("next") == "answer")
        effort = self._ask(
            "assess_effort",
            {
                "request": self._request,
                "step_type": "answer" if answering else "arguments",
                "steps": self._recent_steps(),
                "failures": self._counts,
            },
        )
        if effort:
            self._pending.append(("effort", effort["decision_id"]))

    # ------------------------------------------------------------------ hooks
    def begin(self, request: str, task_id: str) -> None:
        self._reset(request, task_id)
        self._gate_then_effort("start")

    def model_called(self, tool_names: list[str], reply_empty: bool, think: bool | str) -> None:
        """Record what the model did with the decisions asked before this call."""

        def resolve() -> None:
            first = tool_names[0] if tool_names else None
            for kind, decision_id in self._pending:
                if kind == "start":
                    mode = "answer" if first is None else ("write" if first in WRITE_TOOLS else "read")
                    self._log.set_action_taken(decision_id, f"{mode}:{first or 'none'}")
                elif kind == "next":
                    self._log.set_action_taken(decision_id, first or "answer")
                else:
                    outcome = "empty_reply" if reply_empty and not tool_names else None
                    self._log.set_action_taken(decision_id, think_tier(think), outcome)
            self._pending = []
            if reply_empty and not tool_names:
                self._counts["empty_replies"] += 1
            self._step += 1

        self._safely(resolve)

    def before_tool(self, name: str, arguments: dict) -> int | None:
        """gate_write for a write the model is about to run; returns its decision id."""
        if name not in WRITE_TOOLS and name not in BLOCKED_TOOLS:
            return None
        gate = self._ask(
            "gate_write",
            {"request": self._request, "tool": name, "arguments": arguments, "known_ids": sorted(self.known_ids)},
        )
        return gate["decision_id"] if gate else None

    def after_tool(
        self, name: str, arguments: dict, result: ToolResult | None, duration_ms: float, gate_id: int | None = None,
        error: str | None = None,
    ) -> None:
        """Log what actually ran. `result` is None when the call raised (`error` says why) or was refused."""
        ok = result is not None and not result.is_error and error is None
        error = error or (result.text[:500] if result is not None and result.is_error else None)

        def record() -> None:
            self._log.record_tool_call(
                tool=name, arguments=arguments, ok=ok, error=error, duration_ms=duration_ms, context=self._context()
            )
            self._executed.append({"tool": name, "arguments": arguments, "ok": ok, "error": error})
            self._steps.append({"tool": name, "status": "ok" if ok else "error"})
            if not ok:
                self._counts["tool_errors"] += 1
            if result is not None and ok:
                self.known_ids |= find_note_ids(result.text)
            if gate_id is not None:
                self._log.set_action_taken(gate_id, "executed", "ok" if ok else "error")

        self._safely(record)
        if name == "semantic_search" and ok:
            self._safely(lambda: self._rerank(result.text))

    def _rerank(self, text: str) -> None:
        found = json.loads(text)
        if found.get("matches"):
            self._ask("rerank", {"query": found.get("query", ""), "matches": found["matches"]})

    def before_next_call(self) -> None:
        self._gate_then_effort("next")

    def close(self) -> None:
        self._log.close()

    def end(self, reply: str) -> None:
        """check_claim on the reply as the user sees it (in shadow, it is shown regardless)."""
        claim = self._ask(
            "check_claim", {"reply": reply, "log": self._executed, "known_ids": sorted(self.known_ids)}
        )
        if claim:
            self._safely(lambda: self._log.set_action_taken(claim["decision_id"], "shown"))
