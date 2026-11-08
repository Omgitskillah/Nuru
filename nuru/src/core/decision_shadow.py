"""Local execution receipts and deterministic policy audit, with no model decision calls."""

import json
from typing import Any
from collections.abc import Callable
from core.write_checks import WRITE_TOOLS, check_write, find_note_ids
from core.decision_log import DecisionLog
from core.expert import SEARCH_TOOLS, FoundNote, notes_found
from core.providers.mcp_tools import ToolResult

_SUMMARY_CHARS = 160
_ARGUMENT_CHARS = 80
_LISTED_MATCHES = 3


def think_tier(think: bool | str) -> str:
    """The tier name for an Ollama `think` value (True is the model's own default, full thinking)."""
    if think is False:
        return "off"
    if think is True:
        return "high"
    return str(think)


def _one_line(text: str, limit: int = _SUMMARY_CHARS) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_result(tool: str, text: str, ok: bool, error: str | None = None) -> str:
    """One line on what a tool returned - what was found, read or changed - for the execution audit."""
    if not ok:
        return _one_line(f"failed: {error or text}")
    try:
        found = json.loads(text)
    except (TypeError, ValueError):
        found = None
    if isinstance(found, dict):
        items = next(
            (
                found[k]
                for k in ("matches", "notes", "files")
                if isinstance(found.get(k), list)
            ),
            None,
        )
        if items is not None:
            count = found.get("count", len(items))
            names = [
                (m.get("title") or m.get("path") or "")
                + (f" ({m['id']})" if m.get("id") else "")
                if isinstance(m, dict)
                else str(m)
                for m in items[:_LISTED_MATCHES]
            ]
            more = f" and {count - len(names)} more" if count > len(names) else ""
            return _one_line(
                f"{count} found" + (f": {', '.join(names)}{more}" if names else "")
            )
        if "from" in found and "to" in found:
            return _one_line(f"moved {found['from']} to {found['to']}")
        if "path" in found or "id" in found:
            created = " (created)" if found.get("created") else ""
            return _one_line(
                f"done: {found.get('path', '')} {found.get('id') or ''}".strip()
                + created
            )
        if "answer" in found:
            return _one_line(f"answered: {found['answer']}")
    if tool == "read_note":
        return _one_line(f"read {len(text)} characters: {text[:100]}")
    if tool in WRITE_TOOLS:
        return _one_line(f"done: {text}")
    return _one_line(text or "(empty)")


def _short_arguments(arguments: dict) -> dict:
    return {
        k: _one_line(v, _ARGUMENT_CHARS) if isinstance(v, str) else v
        for k, v in arguments.items()
    }


class DecisionShadow:
    def __init__(self, log: DecisionLog, session_id: str):
        self._log, self._session_id = log, session_id
        self.known_ids: set[str] = set()
        self.last_search_ids: list[str] = []
        self.watch_enforced = False
        self._watch_ids: list[int] = []
        self.failures = 0
        self._reset("", None)

    def begin(self, request: str, task_id: str) -> None:
        self._reset(request, task_id)

    def model_called(self, tool_names: list[str], reply_empty: bool, think) -> None:
        self._step += 1

    def before_tool(self, name: str, arguments: dict) -> dict | None:
        return self._code_gate(name, arguments) if name in WRITE_TOOLS else None

    def after_tool(
        self,
        name: str,
        arguments: dict,
        result: ToolResult | None,
        duration_ms: float,
        gate_id: int | None = None,
        error: str | None = None,
    ) -> None:
        ok = result is not None and not result.is_error and error is None
        error = error or (
            result.text[:500] if result is not None and result.is_error else None
        )
        # Evidence tracking is not conditional on logging succeeding.
        if result is not None and ok:
            self.known_ids |= find_note_ids(result.text)
            if name in SEARCH_TOOLS:
                found = notes_found(name, result.text)
                self.notes += found
                self.last_search_ids = [n.id for n in found if n.id]
        summary = summarize_result(
            name, result.text if result is not None else "", ok, error
        )
        self._executed.append(
            {
                "tool": name,
                "arguments": _short_arguments(arguments),
                "ok": ok,
                "result": summary,
            }
        )
        self._steps.append(
            {"tool": name, "status": "ok" if ok else "error", "result": summary}
        )
        self._safely(
            lambda: self._log.record_tool_call(
                tool=name,
                arguments=arguments,
                ok=ok,
                error=error,
                duration_ms=duration_ms,
                context=self._context(),
            )
        )
        if gate_id is not None:
            self._safely(
                lambda: self._log.set_action_taken(
                    gate_id, "executed", "ok" if ok else "error"
                )
            )

    def _reset(self, request: str, task_id: str | None) -> None:
        self._request = request
        self._task_id = task_id
        self._step = 0
        self._steps: list[dict[str, str]] = []
        self.notes: list[
            FoundNote
        ] = []  # titles and IDs the searches found, this turn (for the expert)
        self._executed: list[dict[str, Any]] = []

    def _context(self) -> dict[str, Any]:
        return {
            "session_id": self._session_id,
            "task_id": self._task_id,
            "step": self._step,
        }

    def _safely(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            self.failures += 1

    @property
    def steps(self) -> list[dict[str, str]]:
        return list(self._steps)

    @property
    def executed(self) -> list[dict]:
        return list(self._executed)

    def watch_signal(self, kind: str, tokens: int, detail: str) -> None:
        """A code check fired (budget, repetition): logged as a decision of its own."""

        def record() -> None:
            self._watch_ids.append(
                self._log.record_decision(
                    decision_point="watch_thinking",
                    question_set="watch_thinking_code",
                    question_set_version=1,
                    state={
                        "request": self._request,
                        "thinking_tokens": tokens,
                        "detail": detail,
                    },
                    questions={},
                    status="code_signal",
                    proposed_action=kind,
                    mode="enforce" if self.watch_enforced else "shadow",
                    context=self._context(),
                )
            )

        self._safely(record)

    def repeated_call(
        self, tool: str, arguments: dict, count: int, stopped: bool
    ) -> None:
        """A tool called with the same arguments `count` times this turn: a loop. Logged with what the
        session did about it - stopped the turn (enforced) or let the call run (shadow)."""

        def record() -> None:
            decision_id = self._log.record_decision(
                decision_point="watch_thinking",
                question_set="watch_thinking_code",
                question_set_version=1,
                state={
                    "request": self._request,
                    "tool": tool,
                    "arguments": arguments,
                    "count": count,
                },
                questions={},
                status="code_signal",
                proposed_action="repeated_call",
                mode="enforce" if stopped else "shadow",
                context=self._context(),
            )
            self._log.set_action_taken(
                decision_id, "stopped" if stopped else "continued", f"calls={count}"
            )

        self._safely(record)

    def watch_done(self, steered: bool, thinking_tokens: int) -> None:
        """How the watched call ended: stopped by the watch, or let run to `thinking_tokens`."""
        for decision_id in self._watch_ids:
            self._safely(
                lambda d=decision_id: self._log.set_action_taken(
                    d,
                    "steered" if steered else "continued",
                    f"thinking_tokens={thinking_tokens}",
                )
            )
        self._watch_ids = []

    def _code_gate(self, name: str, arguments: dict) -> dict | None:
        """gate_write's code checks (blocked tools, protected paths, invented note IDs) locally:
        a block recorded in the local audit, or None for a write code has no objection to."""
        reasons = check_write(name, arguments, self.known_ids)
        if not reasons:
            return None
        decision_id = None
        try:
            decision_id = self._log.record_decision(
                decision_point="gate_write",
                question_set="gate_write",
                question_set_version=0,
                state={"request": self._request, "tool": name, "arguments": arguments},
                questions={},
                status="code_blocked",
                mode="enforce",
                context=self._context(),
                detail="; ".join(reasons),
                proposed_action="block",
            )
        except Exception:
            self.failures += 1
        return {
            "decision_id": decision_id,
            "verdict": "block",
            "reasons": reasons,
            "source": "code",
            "status": "code_blocked",
            "probabilities": {},
        }

    def gate_action(
        self, gate: dict | None, action: str, outcome: str | None = None
    ) -> None:
        """Record what the session did with a gate_write verdict it enforced (blocked, asked, ...)."""
        if gate:
            self._safely(
                lambda: self._log.set_action_taken(gate["decision_id"], action, outcome)
            )

    def close(self) -> None:
        self._log.close()
