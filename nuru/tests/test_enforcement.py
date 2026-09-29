"""The enforce paths, end to end in-process: Session -> DecisionShadow -> laya_mcp's tool functions ->
DecisionLayer, with a scripted Laya and a test-only config that marks every threshold calibrated
(the only way the config allows enforce). No model, no vault: chat and the MCP tools are fakes."""

import json

import pytest
from ulid import ULID

from core import session
from core.enforcement import Enforcement, log_lines
from core.expert import ExpertPlanner
from core.providers.mcp_tools import ToolResult
from core.session import Session, Text, Thinking, ToolCall
from laya_mcp import server as laya_server
from laya_mcp.config import load_config
from laya_mcp.decision_log import DecisionLog
from laya_mcp.decisions import DecisionLayer
from laya_mcp.predictor import Prediction
from laya_mcp.question_sets import load_question_sets

PRIYA = str(ULID())
INVENTED = str(ULID())
REQUEST = "Add a line to Priya's note saying she prefers async messages on Fridays."


class ScriptedPredictor:
    """Answers from a script {question: {option: p}} (or a function of the state); unscripted choice
    questions answer their first option at 1.0."""

    def __init__(self, script):
        self.script = script

    def status(self):
        return {"state": "ready"}

    def predict(self, qset, state, questions):
        script = self.script(state) if callable(self.script) else self.script
        answers = {}
        for qid, q in questions.items():
            if q["type"] == "noul":
                answers[qid] = {"type": "noul", "noul": script.get(qid, 0.5)}
                continue
            options = list(q["criteria"])
            p = script.get(qid) or {options[0]: 1.0}
            p = {o: p.get(o, 0.0) for o in options}
            answers[qid] = {"type": q["type"], "choice": max(p, key=p.get), "probabilities": p}
        return Prediction(answers, qset.checkpoint, 10, 1.0)


class Tools:
    """The model's tools, faked; the harness tools are laya_mcp's real functions."""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def connect(self):
        pass

    def list_tools(self):
        return [{"type": "function", "function": {"name": n, "description": "x", "parameters": p}} for n, p in [
            ("semantic_search", {"properties": {"query": {"type": "string"}}, "required": ["query"], "type": "object"}),
            ("append_note", {"properties": {"id": {"type": "string"}, "content": {"type": "string"}},
                             "required": ["id", "content"], "type": "object"}),
        ]]

    def call_tool_result(self, name, arguments):
        self.calls.append((name, arguments))
        return ToolResult(self.results.get(name, "ok"))

    def call_harness_tool(self, name, arguments):
        try:
            return ToolResult(json.dumps(getattr(laya_server, name)(**arguments)))
        except Exception as e:
            return ToolResult(str(e), is_error=True)

    def close(self):
        pass


SEARCH = json.dumps({"query": REQUEST, "matches": [{"path": "nuru/priya-raman.md", "id": PRIYA, "title": "Priya Raman",
                                                    "text": "Priya Raman > Role\n\nFirmware engineer."}]})
DEFAULT_SCRIPT = {
    "mode": {"write": 0.95},
    "next": {"continue": 0.9},
    "tier": {"low": 0.95},
    "matches_request": 0.95,
    "verdict": {"proceed": 0.95},
    "claim": {"made_change": 0.9},
    "relevant": 0.9,
}


def enforce_config(tmp_path, points, **overrides):
    """A test-only config: every threshold marked calibrated, the given points enforced."""
    raw = load_config().model_dump(mode="json")
    for options in raw["thresholds"].values():
        for thresholds in options.values():
            for threshold in thresholds.values():
                threshold["calibration"] = {"date": "2026-09-29", "labels": 100, "held_out": 30}
    raw["database"] = str(tmp_path / "decisions.sqlite3")
    for point in points:
        raw["decision_points"][point] = "enforce"
    raw["tiers"]["models"][session.BASE_MODEL] = {"off": False, "low": "low", "high": "high"}
    for key, value in overrides.items():
        raw[key] = {**raw[key], **value}
    return type(load_config()).model_validate(raw)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    def make(points, responses, script=DEFAULT_SCRIPT, results=None, expert_client=None, **config_overrides):
        config = enforce_config(tmp_path, points, **config_overrides)
        monkeypatch.setattr(session, "THINK", True)
        monkeypatch.setattr(session, "load_decision_config", lambda: config)
        layer = DecisionLayer(config, load_question_sets(), ScriptedPredictor(script), DecisionLog(config.database_path))
        monkeypatch.setattr(laya_server, "_layer", layer)
        seen = []
        responses = iter(responses)

        def chat(model, messages, num_ctx=8192, think=True, tools=None, fmt=None):
            seen.append({"think": think, "tools": tools is not None, "fmt": fmt, "messages": [dict(m) for m in messages]})
            yield from next(responses)

        tools = Tools(results or {"semantic_search": SEARCH, "append_note": "nuru/priya-raman.md"})
        monkeypatch.setattr(session, "ensure_server_running", lambda: None)
        monkeypatch.setattr(session, "ensure_model_loaded", lambda model: None)
        monkeypatch.setattr(session, "chat", chat)
        monkeypatch.setattr(session, "MCPTools", lambda server_params, **kwargs: tools)
        if expert_client is not None:
            monkeypatch.setattr(session, "ExpertPlanner",
                                lambda settings, log, **kwargs: ExpertPlanner(settings, log, expert_client))
        s = Session.start()
        return s, seen, tools

    return make


def call(name, arguments):
    return {"tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


# ---------------------------------------------------------------- everything in shadow
def test_with_nothing_enforced_the_turn_is_the_models(setup):
    s, seen, tools = setup([], [["Hello."]])
    events = list(s.ask("hi"))
    assert events == [Text("Hello.")] and tools.calls == []
    assert seen[0]["think"] is True and seen[0]["tools"]


# ---------------------------------------------------------------- tool_gate
def test_a_read_or_write_request_starts_with_the_harness_running_the_search(setup):
    s, seen, tools = setup(["tool_gate"], [["Priya prefers async on Fridays - noted."]], script={**DEFAULT_SCRIPT, "claim": {"no_change": 0.9}})
    events = list(s.ask(REQUEST))
    assert events[0] == ToolCall("semantic_search", {"query": REQUEST})
    assert tools.calls[0] == ("semantic_search", {"query": REQUEST})
    first_call_messages = seen[0]["messages"]
    assert first_call_messages[-1] == {"role": "tool", "content": SEARCH}


def test_a_confident_answer_gets_no_tools(setup):
    s, seen, tools = setup(["tool_gate"], [["128.4 mAh."]], script={**DEFAULT_SCRIPT, "mode": {"answer": 0.99}})
    list(s.ask("What's 5.35 mA times 24 hours?"))
    assert seen[0]["tools"] is False and tools.calls == []


def test_the_step_cap_stops_the_turn_when_there_is_no_expert(setup):
    s, seen, tools = setup(["tool_gate"], [[call("semantic_search", {"query": "a"})]] * 3,
                           script={**DEFAULT_SCRIPT, "mode": {"read": 0.9}}, tool_gate={"step_cap": 2})
    events = list(s.ask("Find it"))
    assert isinstance(events[-1], Text) and events[-1].text.startswith("I stopped here: it reached the 2-step limit")
    assert "expert isn't available" in events[-1].text


# ---------------------------------------------------------------- assess_effort
def test_the_thinking_level_comes_from_the_tier_policy(setup):
    s, seen, _ = setup(["assess_effort"], [["Hi."]])
    list(s.ask("hi"))
    assert seen[0]["think"] == "low"


def test_an_empty_reply_moves_the_tier_up_instead_of_turning_thinking_off(setup):
    s, seen, _ = setup(["assess_effort"], [[{"thinking": "the answer is 42"}], ["42"]])
    list(s.ask("What is six times seven?"))
    assert [c["think"] for c in seen] == ["low", "high"]


def test_past_the_highest_local_tier_the_expert_plans(setup, monkeypatch):
    from types import SimpleNamespace

    client = SimpleNamespace(calls=[])

    def create(**kwargs):
        client.calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="1. semantic_search")])

    client.beta = SimpleNamespace(messages=SimpleNamespace(create=create))
    s, seen, _ = setup(["assess_effort"], [[{"thinking": "hmm"}], [{"thinking": "hmm"}], ["Done."]],
                       script={**DEFAULT_SCRIPT, "tier": {"high": 0.95}}, expert_client=client)
    events = list(s.ask("Something hard"))
    assert Thinking("Plan from the expert:\n1. semantic_search") in events
    assert "Request: Something hard" in client.calls[0]["messages"][0]["content"]
    assert seen[-1]["messages"][-1]["content"].startswith("[Nuru] A plan from the expert")


# ---------------------------------------------------------------- gate_write
def test_a_write_runs_with_forced_arguments_limited_to_searched_ids(setup):
    responses = [
        [call("semantic_search", {"query": "Priya"})],
        [call("append_note", {"id": INVENTED, "content": "Prefers async"})],
        [json.dumps({"id": PRIYA, "content": "Prefers async messages on Fridays"})],  # the forced-schema call
        ["Added it to Priya's note."],
    ]
    s, seen, tools = setup(["gate_write"], responses)
    list(s.ask(REQUEST))
    forced = next(c for c in seen if c["fmt"] is not None)
    assert forced["fmt"]["anyOf"][0]["properties"]["id"]["enum"] == [PRIYA]
    assert ("append_note", {"id": PRIYA, "content": "Prefers async messages on Fridays"}) in tools.calls
    assert not any(args.get("id") == INVENTED for name, args in tools.calls)


def test_a_write_whose_own_arguments_are_valid_runs_without_being_asked_again(setup):
    responses = [
        [call("semantic_search", {"query": "Priya"})],
        [call("append_note", {"id": PRIYA, "content": "Prefers async messages on Fridays"})],
        ["Added it to Priya's note."],
    ]
    s, seen, tools = setup(["gate_write"], responses)
    list(s.ask(REQUEST))
    assert all(c["fmt"] is None for c in seen)  # no forced-schema call
    assert ("append_note", {"id": PRIYA, "content": "Prefers async messages on Fridays"}) in tools.calls


def test_a_write_that_needs_confirmation_is_blocked_when_nobody_is_there(setup):
    responses = [
        [call("semantic_search", {"query": "Priya"})],
        [call("append_note", {"id": PRIYA, "content": "Prefers async"})],
        ["I couldn't add it."],
    ]
    s, seen, tools = setup(["gate_write"], responses, script={**DEFAULT_SCRIPT, "verdict": {"confirm": 0.9}})
    list(s.ask(REQUEST))
    assert not any(name == "append_note" for name, _ in tools.calls)
    tool_message = next(m for m in seen[-1]["messages"] if m["role"] == "tool" and "append_note" in m["content"])
    assert "nobody was there to give it" in tool_message["content"]


def test_a_confirmed_write_runs(setup):
    responses = [
        [call("semantic_search", {"query": "Priya"})],
        [call("append_note", {"id": PRIYA, "content": "Prefers async"})],
        ["Added."],
    ]
    s, _, tools = setup(["gate_write"], responses, script={**DEFAULT_SCRIPT, "verdict": {"confirm": 0.9}})
    s.confirm_write = lambda name, args: True
    list(s.ask(REQUEST))
    assert ("append_note", {"id": PRIYA, "content": "Prefers async"}) in tools.calls


def test_cannot_do_is_a_failure_and_nothing_is_written(setup):
    responses = [
        [call("semantic_search", {"query": "Zed"})],
        [call("append_note", {"id": INVENTED, "content": "hi"})],
        [json.dumps({"cannot_do": "no note for Zed"})],
        ["I couldn't find Zed's note."],
    ]
    s, seen, tools = setup(["gate_write"], responses, results={"semantic_search": json.dumps({"matches": []})})
    list(s.ask("Add hi to Zed's note"))
    assert not any(name == "append_note" for name, _ in tools.calls)
    assert json.loads(json.dumps(seen[2]["fmt"])) == {"anyOf": [{"type": "object", "properties": {"cannot_do": {"type": "string"}},
                                                                "required": ["cannot_do"], "additionalProperties": False}]}


# ---------------------------------------------------------------- check_claim
def test_a_checked_reply_is_shown_only_after_the_check(setup):
    s, _, _ = setup(["check_claim"], [["Nothing about Kubernetes in your notes."]], script={**DEFAULT_SCRIPT, "claim": {"no_change": 0.9}})
    assert list(s.ask("Kubernetes?")) == [Text("Nothing about Kubernetes in your notes.")]


def test_a_false_claim_is_regenerated_with_the_log_in_view(setup):
    script = lambda state: {**DEFAULT_SCRIPT, "claim": {"made_change": 0.9} if "added" in state.get("reply", "") else {"reports_failure": 0.9}}
    s, seen, _ = setup(["check_claim"], [["I added it to Priya's note."], ["I didn't change anything - no write ran."]], script=script)
    events = list(s.ask(REQUEST))
    assert events == [Text("I didn't change anything - no write ran.")]
    assert seen[-1]["messages"][-1]["content"].startswith("[Nuru] Your reply said something the log")


def test_a_reply_that_fails_twice_is_shown_with_a_warning_and_the_log(setup):
    s, _, _ = setup(["check_claim"], [["I added it."], ["I added it, really."]])
    [event] = list(s.ask(REQUEST))
    assert event.text.startswith("I added it, really.") and "**Check this reply:**" in event.text
    assert "claims a change the log does not show" in event.text


def test_log_lines_read_plainly():
    assert log_lines([]) == "- nothing was run"
    assert log_lines([{"tool": "append_note", "ok": False, "result": "failed: timeout"}]) == \
        "- append_note: FAILED - failed: timeout"


def test_enforcement_is_off_until_a_point_is_enforced(tmp_path):
    enforcement = Enforcement(enforce_config(tmp_path, []), session.BASE_MODEL, None)
    assert enforcement.any is False and enforcement.offer_tools({"mode": "answer"}) is True
