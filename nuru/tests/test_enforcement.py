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
            ("search", {"properties": {"query": {"type": "string"}}, "required": ["query"], "type": "object"}),
            ("write_note", {"properties": {"content": {"type": "string"},
                                           "note": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}},
                            "required": ["content"], "type": "object"}),
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
    raw["decision_points"] = {point: "shadow" for point in raw["decision_points"]}
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

        tools = Tools(results or {"search": SEARCH, "write_note": "nuru/priya-raman.md"})
        monkeypatch.setattr(session, "create_model", lambda model, base_model, modelfile_path: None)
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
    assert events[0] == ToolCall("search", {"query": REQUEST})
    assert tools.calls[0] == ("search", {"query": REQUEST})
    first_call_messages = seen[0]["messages"]
    assert first_call_messages[-1] == {"role": "tool", "content": SEARCH}


def test_a_confident_answer_gets_no_tools(setup):
    s, seen, tools = setup(["tool_gate"], [["128.4 mAh."]], script={**DEFAULT_SCRIPT, "mode": {"answer": 0.99}})
    list(s.ask("What's 5.35 mA times 24 hours?"))
    assert seen[0]["tools"] is False and tools.calls == []


def test_the_step_cap_stops_the_turn_when_there_is_no_expert(setup):
    s, seen, tools = setup(["step_cap"], [[call("search", {"query": q})] for q in "abc"],
                           tool_gate={"step_cap": 2})
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
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="1. search")])

    client.beta = SimpleNamespace(messages=SimpleNamespace(create=create))
    s, seen, _ = setup(["assess_effort"], [[{"thinking": "hmm"}], [{"thinking": "hmm"}], ["Done."]],
                       script={**DEFAULT_SCRIPT, "tier": {"high": 0.95}}, expert_client=client)
    events = list(s.ask("Something hard"))
    assert Thinking("Plan from the expert:\n1. search") in events
    assert "Request: Something hard" in client.calls[0]["messages"][0]["content"]
    assert seen[-1]["messages"][-1]["content"].startswith("[Nuru] A plan from the expert")


# ---------------------------------------------------------------- gate_write
def test_a_write_runs_with_forced_arguments_limited_to_searched_ids(setup):
    responses = [
        [call("search", {"query": "Priya"})],
        [call("write_note", {"note": INVENTED, "content": "Prefers async"})],
        [json.dumps({"note": PRIYA, "content": "Prefers async messages on Fridays"})],  # the forced-schema call
        ["Added it to Priya's note."],
    ]
    s, seen, tools = setup(["gate_write"], responses)
    list(s.ask(REQUEST))
    forced = next(c for c in seen if c["fmt"] is not None)
    assert forced["messages"][-1]["content"].startswith("[Nuru] Next step: `write_note`")
    assert ("write_note", {"note": PRIYA, "content": "Prefers async messages on Fridays"}) in tools.calls
    assert not any(args.get("note") == INVENTED for name, args in tools.calls)


def test_a_write_whose_own_arguments_are_valid_runs_without_being_asked_again(setup):
    responses = [
        [call("search", {"query": "Priya"})],
        [call("write_note", {"note": PRIYA, "content": "Prefers async messages on Fridays"})],
        ["Added it to Priya's note."],
    ]
    s, seen, tools = setup(["gate_write"], responses)
    list(s.ask(REQUEST))
    assert all(c["fmt"] is None for c in seen)  # no forced-schema call
    assert ("write_note", {"note": PRIYA, "content": "Prefers async messages on Fridays"}) in tools.calls


def test_a_write_that_needs_confirmation_is_blocked_when_nobody_is_there(setup):
    responses = [
        [call("search", {"query": "Priya"})],
        [call("write_note", {"note": PRIYA, "content": "Prefers async"})],
        ["I couldn't add it."],
    ]
    s, seen, tools = setup(["gate_write"], responses, script={**DEFAULT_SCRIPT, "verdict": {"confirm": 0.9}})
    list(s.ask(REQUEST))
    assert not any(name == "write_note" for name, _ in tools.calls)
    tool_message = next(m for m in seen[-1]["messages"] if m["role"] == "tool" and "write_note" in m["content"])
    assert "nobody was there to give it" in tool_message["content"]


def test_a_confirmed_write_runs(setup):
    responses = [
        [call("search", {"query": "Priya"})],
        [call("write_note", {"note": PRIYA, "content": "Prefers async"})],
        ["Added."],
    ]
    s, _, tools = setup(["gate_write"], responses, script={**DEFAULT_SCRIPT, "verdict": {"confirm": 0.9}})
    s.confirm_write = lambda name, args: True
    list(s.ask(REQUEST))
    assert ("write_note", {"note": PRIYA, "content": "Prefers async"}) in tools.calls


def test_cannot_do_is_a_failure_and_nothing_is_written(setup):
    responses = [
        [call("search", {"query": "Zed"})],
        [call("write_note", {"note": INVENTED, "content": "hi"})],
        [json.dumps({"cannot_do": "no note for Zed"})],
        ["I couldn't find Zed's note."],
    ]
    s, seen, tools = setup(["gate_write"], responses, results={"search": json.dumps({"matches": []})})
    list(s.ask("Add hi to Zed's note"))
    assert not any(name == "write_note" for name, _ in tools.calls)
    assert {"type": "object", "properties": {"cannot_do": {"type": "string"}}, "required": ["cannot_do"],
            "additionalProperties": False} in seen[2]["fmt"]["anyOf"]


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
    assert log_lines([{"tool": "write_note", "ok": False, "result": "failed: timeout"}]) == \
        "- write_note: FAILED - failed: timeout"


def test_enforcement_is_off_until_a_point_is_enforced(tmp_path):
    enforcement = Enforcement(enforce_config(tmp_path, []), session.BASE_MODEL, None)
    assert enforcement.any is False and enforcement.offer_tools({"mode": "answer"}) is True


# ---------------------------------------------------------------- watch_thinking
RUNAWAY = [{"thinking_chunk": f"Thought {i}: adding load {i}, recomputing the totals from the table once more. "} for i in range(900)]


def watch_rows(tmp_path):
    log = DecisionLog(tmp_path / "decisions.sqlite3")
    try:
        return [dict(r) for r in log._conn.execute("SELECT * FROM decisions WHERE decision_point = 'watch_thinking' ORDER BY id")]
    finally:
        log.close()


def test_in_shadow_a_runaway_is_logged_and_left_to_run(setup, tmp_path):
    s, seen, _ = setup([], [RUNAWAY + ["5.35 mA."]], script={**DEFAULT_SCRIPT, "state": {"progressing": 0.9}})
    events = list(s.ask("Recompute the average current"))
    assert events[-1] == Text("5.35 mA.")  # the turn is the model's
    code = [r for r in watch_rows(tmp_path) if r["status"] == "code_signal"]
    assert code[0]["proposed_action"] == "budget" and code[0]["action_taken"] == "continued"
    assert code[0]["outcome"].startswith("thinking_tokens=")


def test_enforced_a_runaway_is_stopped_and_the_model_asked_to_act(setup, tmp_path):
    from core.session import Steered

    s, seen, _ = setup(["watch_thinking"], [RUNAWAY + ["never reached"], ["5.35 mA."]],
                       script={**DEFAULT_SCRIPT, "state": {"progressing": 0.9}})
    events = list(s.ask("Recompute the average current"))
    assert Steered("budget") in events and events[-1] == Text("5.35 mA.")
    assert seen[1]["think"] is False
    assert seen[1]["messages"][-1]["content"].startswith("[Nuru] Stop reasoning now")
    assert watch_rows(tmp_path)[0]["action_taken"] == "steered"


def test_enforced_laya_can_stop_reasoning_that_is_ready(setup):
    from core.session import Steered

    chunks = [{"thinking_chunk": f"Point {i} checks out against the note's figures. "} for i in range(200)]
    s, seen, _ = setup(["watch_thinking", "watch_laya"], [chunks + ["late"], ["Done."]],
                       script={**DEFAULT_SCRIPT, "state": {"ready": 0.95}})
    events = list(s.ask("Check my notes"))
    assert Steered("ready") in events and events[-1] == Text("Done.")


def test_with_only_the_code_checks_enforced_laya_s_signal_is_logged_not_acted_on(setup):
    chunks = [{"thinking_chunk": f"Point {i} checks out against the note's figures. "} for i in range(200)]
    s, seen, _ = setup(["watch_thinking"], [chunks + ["Done."]], script={**DEFAULT_SCRIPT, "state": {"ready": 0.95}})
    events = list(s.ask("Check my notes"))
    assert events[-1] == Text("Done.") and len(seen) == 1


# ---------------------------------------------------------------- write_checks
def test_enforced_write_checks_stop_a_write_to_an_invented_note_id(setup):
    invented = "01ZZZZZZZZZZZZZZZZZZZZZZZZ"
    s, seen, tools = setup(["write_checks"], [[call("write_note", {"note": invented, "content": "x"})], ["I couldn't."]])
    events = list(s.ask("Add x to Priya's note"))
    assert tools.calls == []  # never reached the server
    assert "write_note was not run" in seen[1]["messages"][-1]["content"]
    assert events[-1] == Text("I couldn't.")


def test_write_checks_let_a_write_to_a_searched_note_through(setup):
    s, seen, tools = setup(["write_checks"], [[call("search", {"query": "Priya"})],
                                              [call("write_note", {"note": PRIYA, "content": "x"})], ["Done."]])
    list(s.ask("Add x to Priya's note"))
    assert [name for name, _ in tools.calls] == ["search", "write_note"]


def test_the_repeated_call_stop_ignores_case(setup):
    reads = [[call("read_note", {"name": name})] for name in ("nuru/Indexes/kb.md", "nuru/indexes/kb.md", "NURU/INDEXES/KB.MD")]
    s, seen, tools = setup(["watch_thinking"], reads + [["never reached"]])
    events = list(s.ask("Read the index"))
    assert len(tools.calls) == 2 and "the same arguments 3 times" in events[-1].text


def test_after_a_steer_only_the_next_call_goes_without_thinking(setup):
    s, seen, _ = setup(["watch_thinking"], [RUNAWAY + ["never reached"], [call("search", {"query": "loads"})],
                                            ["5.35 mA."]],
                       script={**DEFAULT_SCRIPT, "state": {"progressing": 0.9}})
    events = list(s.ask("Recompute the average current"))
    assert events[-1] == Text("5.35 mA.")
    assert [c["think"] for c in seen] == [True, False, True]


def test_in_shadow_a_repeated_call_is_logged_once_and_runs(setup, tmp_path):
    same = [call("search", {"query": "loads"})]
    s, seen, tools = setup([], [same, same, same, same, ["Done."]])
    events = list(s.ask("Find the loads"))
    assert events[-1] == Text("Done.") and len(tools.calls) == 4
    rows = [r for r in watch_rows(tmp_path) if r["proposed_action"] == "repeated_call"]
    assert len(rows) == 1 and rows[0]["action_taken"] == "continued" and rows[0]["outcome"] == "calls=3"


def test_enforced_a_repeated_call_stops_the_turn_before_it_runs(setup, tmp_path):
    same = [call("search", {"query": "loads"})]
    other = [call("search", {"query": "other loads"})]
    s, seen, tools = setup(["watch_thinking"], [same, other, same, same, ["never reached"]])
    events = list(s.ask("Find the loads"))
    assert len(tools.calls) == 3
    assert events[-1].text.startswith("I stopped here: it called search with the same arguments 3 times")
    rows = [r for r in watch_rows(tmp_path) if r["proposed_action"] == "repeated_call"]
    assert rows[0]["action_taken"] == "stopped"
