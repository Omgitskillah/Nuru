"""The session's hooks into the decision layer: at every decision point, ask Laya what it would do,
log that next to what Nuru actually did, and hand the answer back. In shadow mode (every decision
point, until one is set to enforce) the session only records it; in enforce mode the session acts
on it (core.enforcement). Nothing in here changes a turn by itself.

The session calls these hooks as a turn runs:

    begin(request, task_id)        tool_gate (start) and assess_effort, before the first model call
    model_called(tools, ...)       what the model did - resolves the pending decisions' action_taken
    before_tool / after_tool       gate_write for a write; the execution log; rerank for search
    before_next_call()             tool_gate (next) and assess_effort, after a round of tool results
    end(reply)                     check_claim on the reply the user is shown; the turn's mode

Every step Laya sees carries a one-line summary of what the tool returned (`summarize_result`):
without it, "is that enough to answer?" and "did the reply claim something that didn't happen?" are
unanswerable - v1 of both question sets saw only tool names and statuses.

Every call to the decision layer goes through `_ask`, which swallows any failure - Laya down, the
server gone, a bad answer - so a shadow problem can never break or change a turn. The execution log
(`tool_calls`) is written from what the harness ran, never from what the model said.
"""

import json
from pathlib import PurePosixPath
from collections.abc import Callable
from typing import Any

from dataclasses import dataclass

from laya_mcp.checks import WRITE_TOOLS, find_note_ids
from laya_mcp.decision_log import DecisionLog

from core.expert import SEARCH_TOOLS, FoundNote, notes_found
from core.providers.mcp_tools import ToolResult

# The tool_gate/assess_effort state carries only this many recent steps (tool, status and a short
# summary of the result), plus how many came before - a bounded state, rather than one that grows
# until Laya's budget rejects every decision in a long turn.
_RECENT_STEPS = 6
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
    """One line on what a tool returned - what was found, read or changed - for Laya's state."""
    if not ok:
        return _one_line(f"failed: {error or text}")
    try:
        found = json.loads(text)
    except (TypeError, ValueError):
        found = None
    if isinstance(found, dict):
        items = next((found[k] for k in ("matches", "notes", "files") if isinstance(found.get(k), list)), None)
        if items is not None:
            count = found.get("count", len(items))
            names = [
                (m.get("title") or m.get("path") or "") + (f" ({m['id']})" if m.get("id") else "")
                if isinstance(m, dict) else str(m)
                for m in items[:_LISTED_MATCHES]
            ]
            more = f" and {count - len(names)} more" if count > len(names) else ""
            return _one_line(f"{count} found" + (f": {', '.join(names)}{more}" if names else ""))
        if "from" in found and "to" in found:
            return _one_line(f"moved {found['from']} to {found['to']}")
        if "path" in found or "id" in found:
            created = " (created)" if found.get("created") else ""
            return _one_line(f"done: {found.get('path', '')} {found.get('id') or ''}".strip() + created)
        if "answer" in found:
            return _one_line(f"answered: {found['answer']}")
    if tool == "read_note":
        return _one_line(f"read {len(text)} characters: {text[:100]}")
    if tool in WRITE_TOOLS:
        return _one_line(f"done: {text}")
    return _one_line(text or "(empty)")


def _short_arguments(arguments: dict) -> dict:
    return {k: _one_line(v, _ARGUMENT_CHARS) if isinstance(v, str) else v for k, v in arguments.items()}


@dataclass(frozen=True)
class StepDecisions:
    """What the decision layer said before a model call; either is None if its call failed."""

    gate: dict | None
    effort: dict | None


class DecisionShadow:
    def __init__(
        self,
        call: Callable[[str, dict], ToolResult],
        log: DecisionLog,
        session_id: str,
    ):
        self._call = call
        self._log = log
        self._session_id = session_id
        self.known_ids: set[str] = set()  # every note ID a tool result has shown, this session
        self.last_search_ids: list[str] = []  # the IDs the most recent search returned
        self.watch_enforced = False  # set by the session: whether a watch signal stops the call
        self.effort_off = False  # set by the session: assess_effort is `off`, so it isn't asked
        self.note_type_off = False  # set by the session: note_type is `off`, so it isn't asked
        self._watch_ids: list[int] = []  # watch_thinking decisions of the call in progress
        self.failures = 0  # decision-layer calls that failed; the turns went on regardless
        self._reset("", None)

    def _reset(self, request: str, task_id: str | None) -> None:
        self._request = request
        self._task_id = task_id
        self._step = 0
        self._steps: list[dict[str, str]] = []
        self._counts = {"tool_errors": 0, "empty_replies": 0}
        self.notes: list[FoundNote] = []  # titles and IDs the searches found, this turn (for the expert)
        self._executed: list[dict[str, Any]] = []
        self._pending: list[tuple[str, int]] = []  # (kind, decision_id) awaiting what the model did
        self._start_id: int | None = None  # the turn's mode decision, resolved when the turn ends

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

    def _gate_then_effort(self, phase: str) -> StepDecisions:
        gate = self._ask("tool_gate", {"phase": phase, "request": self._request, "steps": self._recent_steps()})
        if gate and phase == "start":
            self._start_id = gate["decision_id"]
        elif gate:
            self._pending.append((phase, gate["decision_id"]))
        answering = gate is not None and (gate.get("mode") == "answer" or gate.get("next") == "answer")
        effort = None if self.effort_off else self._ask(
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
        return StepDecisions(gate, effort)

    # ------------------------------------------------------------------ hooks
    def begin(self, request: str, task_id: str) -> StepDecisions:
        self._reset(request, task_id)
        return self._gate_then_effort("start")

    def note_failure(self, kind: str) -> None:
        """A failure signal the session saw (cannot_do, schema_failures, claim_failures) - counted
        into what assess_effort is told."""
        self._counts[kind] = self._counts.get(kind, 0) + 1

    @property
    def steps(self) -> list[dict[str, str]]:
        return list(self._steps)

    @property
    def executed(self) -> list[dict]:
        return list(self._executed)

    def model_called(self, tool_names: list[str], reply_empty: bool, think: bool | str) -> None:
        """Record what the model did with the decisions asked before this call."""

        def resolve() -> None:
            for kind, decision_id in self._pending:
                if kind == "next":
                    self._log.set_action_taken(decision_id, "continue" if tool_names else "answer")
                else:
                    outcome = "empty_reply" if reply_empty and not tool_names else None
                    self._log.set_action_taken(decision_id, think_tier(think), outcome)
            self._pending = []
            if reply_empty and not tool_names:
                self._counts["empty_replies"] += 1
            self._step += 1

        self._safely(resolve)

    # ------------------------------------------------------------------ watch_thinking
    def watch(self, thinking_tail: str) -> dict | None:
        """watch_thinking on the latest stretch of the model's reasoning (the last two steps for
        context - the thinking is the bulk of what fits)."""
        answer = self._ask(
            "watch_thinking", {"request": self._request, "steps": self._steps[-2:], "thinking": thinking_tail}
        )
        if answer:
            self._watch_ids.append(answer["decision_id"])
        return answer

    def watch_signal(self, kind: str, tokens: int, detail: str) -> None:
        """A code check fired (budget, repetition): logged as a decision of its own."""

        def record() -> None:
            self._watch_ids.append(self._log.record_decision(
                decision_point="watch_thinking", question_set="watch_thinking_code", question_set_version=1,
                state={"request": self._request, "thinking_tokens": tokens, "detail": detail},
                questions={}, status="code_signal", proposed_action=kind,
                mode="enforce" if self.watch_enforced else "shadow", context=self._context(),
            ))

        self._safely(record)

    def repeated_call(self, tool: str, arguments: dict, count: int, stopped: bool) -> None:
        """A tool called with the same arguments `count` times this turn: a loop. Logged with what the
        session did about it - stopped the turn (enforced) or let the call run (shadow)."""

        def record() -> None:
            decision_id = self._log.record_decision(
                decision_point="watch_thinking", question_set="watch_thinking_code", question_set_version=1,
                state={"request": self._request, "tool": tool, "arguments": arguments, "count": count},
                questions={}, status="code_signal", proposed_action="repeated_call",
                mode="enforce" if stopped else "shadow", context=self._context(),
            )
            self._log.set_action_taken(decision_id, "stopped" if stopped else "continued", f"calls={count}")

        self._safely(record)

    def watch_done(self, steered: bool, thinking_tokens: int) -> None:
        """How the watched call ended: stopped by the watch, or let run to `thinking_tokens`."""
        for decision_id in self._watch_ids:
            self._safely(lambda d=decision_id: self._log.set_action_taken(
                d, "steered" if steered else "continued", f"thinking_tokens={thinking_tokens}"))
        self._watch_ids = []

    def before_tool(self, name: str, arguments: dict) -> dict | None:
        """gate_write for a write about to run: its verdict, or None for a read (or a failed call)."""
        if name not in WRITE_TOOLS:
            return None
        return self._ask(
            "gate_write",
            {"request": self._request, "tool": name, "arguments": arguments, "known_ids": sorted(self.known_ids)},
        )

    def note_type(self, arguments: dict) -> dict | None:
        """Laya's type for a write_note that may create a note (anything but today's daily), with the
        model's own choice recorded against it as the action - "model:<type>", or "model:none"."""
        if self.note_type_off or arguments.get("type") == "daily" or not arguments.get("note"):
            return None
        title = PurePosixPath(str(arguments["note"]).removesuffix(".md")).name
        typed = self._ask("note_type", {"title": title, "content": str(arguments.get("content") or "")[:8000]})
        if typed:
            model = arguments.get("type") or "none"
            self._safely(lambda: self._log.set_action_taken(typed["decision_id"], f"model:{model}"))
        return typed

    def gate_action(self, gate: dict | None, action: str, outcome: str | None = None) -> None:
        """Record what the session did with a gate_write verdict it enforced (blocked, asked, ...)."""
        if gate:
            self._safely(lambda: self._log.set_action_taken(gate["decision_id"], action, outcome))

    def after_tool(
        self, name: str, arguments: dict, result: ToolResult | None, duration_ms: float, gate_id: int | None = None,
        error: str | None = None,
    ) -> str | None:
        """Log what actually ran. `result` is None when the call raised (`error` says why) or was refused.
        For a search, returns the result (JSON) with its matches by meaning reranked, if rerank answered, else None."""
        ok = result is not None and not result.is_error and error is None
        error = error or (result.text[:500] if result is not None and result.is_error else None)

        def record() -> None:
            self._log.record_tool_call(
                tool=name, arguments=arguments, ok=ok, error=error, duration_ms=duration_ms, context=self._context()
            )
            summary = summarize_result(name, result.text if result is not None else "", ok, error)
            self._executed.append({"tool": name, "arguments": _short_arguments(arguments), "ok": ok, "result": summary})
            self._steps.append({"tool": name, "status": "ok" if ok else "error", "result": summary})
            if not ok:
                self._counts["tool_errors"] += 1
            if result is not None and ok:
                self.known_ids |= find_note_ids(result.text)
                if name in SEARCH_TOOLS:
                    self.notes += notes_found(name, result.text)
                    self.last_search_ids = [n.id for n in notes_found(name, result.text) if n.id]
            if gate_id is not None:
                self._log.set_action_taken(gate_id, "executed", "ok" if ok else "error")

        self._safely(record)
        if name == "search" and ok:
            try:
                return self._rerank(result.text)
            except Exception:
                self.failures += 1
        return None

    def _rerank(self, text: str) -> str | None:
        """Only the matches by meaning are reranked: an exact match contains every word, and stays first."""
        found = json.loads(text)
        matches = found.get("matches") or []
        meaning = [m for m in matches if m.get("match") == "meaning"]
        if not meaning:
            return None
        reranked = self._ask("rerank", {"query": found.get("query", ""), "matches": meaning})
        if not reranked or not reranked.get("reranked"):
            return None
        exact = [m for m in matches if m.get("match") != "meaning"]
        return json.dumps({**found, "matches": exact + reranked["matches"]})

    def before_next_call(self) -> StepDecisions:
        return self._gate_then_effort("next")

    def close(self) -> None:
        self._log.close()

    def end(self, reply: str) -> dict | None:
        """check_claim on the reply as the user sees it (in shadow, it is shown regardless), then
        finish the turn. Returns check_claim's verdict."""
        claim = self.check_reply(reply, "shown")
        self.finish()
        return claim

    def check_reply(self, reply: str, action: str) -> dict | None:
        """check_claim on a drafted reply; `action` is what the session will do with it."""
        claim = self._ask(
            "check_claim", {"reply": reply, "log": self._executed, "known_ids": sorted(self.known_ids)}
        )
        if claim:
            self._safely(lambda: self._log.set_action_taken(claim["decision_id"], action))
        return claim

    def record_claim_action(self, claim: dict | None, action: str) -> None:
        if claim:
            self._safely(lambda: self._log.set_action_taken(claim["decision_id"], action))

    def finish(self) -> None:
        """The turn's mode as it turned out: `write` if the model wrote (or tried to), `read` if it
        only used other tools, `answer` if none - judged over the whole turn, since a write rightly
        starts with a search."""
        if self._start_id is not None:
            tools = [call["tool"] for call in self._executed]
            mode = "write" if any(t in WRITE_TOOLS for t in tools) else "read" if tools else "answer"
            self._safely(lambda: self._log.set_action_taken(self._start_id, mode))
