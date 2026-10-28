from core.modelfile import render_modelfile
"""The shadow, end to end in-process: Session -> DecisionShadow -> laya_mcp's tool functions ->
DecisionLayer -> the decision log, with a stub in place of Laya's weights. What must hold: a turn
is exactly what it was without the shadow, and the log records Laya's choice next to Nuru's."""

import json

import pytest
from ulid import ULID

from core import session
from core.decision_shadow import LAYA_POINTS, DecisionShadow, summarize_result, think_tier
from core.providers.mcp_tools import ToolResult
from core.session import Session, Text, ToolCall
from laya_mcp import server as laya_server
from laya_mcp.config import load_config
from laya_mcp.decision_log import DecisionLog
from laya_mcp.decisions import DecisionLayer
from laya_mcp.predictor import Prediction
from laya_mcp.question_sets import load_question_sets

NOTE_ID = str(ULID())


class StubPredictor:
    """Answers every choice with its first option and every noul with 0.5 - enough to exercise the
    plumbing; what Laya actually answers is the evals' business, not these tests'."""

    def status(self):
        return {"state": "ready"}

    def predict(self, qset, state, questions):
        answers = {}
        for qid, q in questions.items():
            if q["type"] == "noul":
                answers[qid] = {"type": "noul", "noul": 0.5}
            else:
                options = list(q["criteria"])
                answers[qid] = {"type": q["type"], "choice": options[0],
                                "probabilities": {o: (1.0 if i == 0 else 0.0) for i, o in enumerate(options)}}
        return Prediction(answers, qset.checkpoint, 10, 1.0)


class ShadowedTools:
    """The model's tools, faked; the harness tools are laya_mcp's real tool functions."""

    retired, unavailable, cancel = None, (), None  # MCPTools' own state, read by Session

    def __init__(self, tools, results, laya_down=False):
        self._tools = tools
        self._results = results
        self.laya_down = laya_down
        self.model_calls = []
        self.harness_calls = []

    def connect(self):
        pass

    def list_tools(self):
        return self._tools

    def call_tool_result(self, name, arguments):
        self.model_calls.append((name, arguments))
        result = self._results.get(name, "")
        return result if isinstance(result, ToolResult) else ToolResult(result)

    def call_harness_tool(self, name, arguments):
        if name == "note_info":  # the notes server's lookup: these tests' notes count as new ones
            return ToolResult(f"Error executing tool note_info: FileNotFoundError: No note found named '{arguments['name']}'.", is_error=True)
        self.harness_calls.append(name)
        if self.laya_down:
            raise ConnectionError("laya-mcp is gone")
        try:
            return ToolResult(json.dumps(getattr(laya_server, name)(**arguments)))
        except Exception as e:
            return ToolResult(str(e), is_error=True)

    def close(self):
        pass


def definition(name, description="Does a thing. More detail."):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {}}}


TOOLS = [definition("search"), definition("find_in_notes"), definition("write_note"), definition("read_note")]


@pytest.fixture
def decision_log(monkeypatch, tmp_path):
    """The layer the Laya server would run, pointed at the same temp log the session opens."""
    monkeypatch.setattr(session, "THINK", True)  # not whatever the developer's .env says
    path = tmp_path / "decisions.sqlite3"
    config = load_config()
    config = config.model_copy(update={"database": path, "decision_points": {p: "shadow" for p in config.decision_points}})
    monkeypatch.setattr(session, "load_decision_config", lambda: config)
    monkeypatch.setattr(laya_server, "_layer", DecisionLayer(config, load_question_sets(), StubPredictor(), DecisionLog(path)))
    return path


def start(monkeypatch, responses, tools):
    responses = iter(responses)

    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        yield from next(responses)

    monkeypatch.setattr(session, "create_model", lambda model, base_model, modelfile_path: render_modelfile(modelfile_path, base_model))
    monkeypatch.setattr(session, "ensure_server_running", lambda: None)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model, *_: None)
    monkeypatch.setattr(session, "chat", chat)
    monkeypatch.setattr(session, "MCPTools", lambda server_params, **kwargs: tools)
    return Session.start()


def call(name, arguments):
    return {"function": {"name": name, "arguments": arguments}}


def rows(path, table="decisions"):
    log = DecisionLog(path)
    try:
        return [dict(r) for r in log._conn.execute(f"SELECT * FROM {table} ORDER BY id")]
    finally:
        log.close()


A_WRITE_TURN = [
    [{"tool_calls": [call("search", {"query": "Priya"})]}],
    [{"tool_calls": [call("write_note", {"note": NOTE_ID, "content": "Prefers async on Fridays"})]}],
    ["Done - added it to ", f"{NOTE_ID}."],
]
RESULTS = {"search": json.dumps({"matches": [{"id": NOTE_ID, "title": "Priya"}]}), "write_note": "ok"}


def test_every_decision_point_is_logged_next_to_what_nuru_did(monkeypatch, decision_log):
    tools = ShadowedTools(TOOLS, RESULTS)
    s = start(monkeypatch, A_WRITE_TURN, tools)
    list(s.ask("Add a line to Priya's note saying she prefers async messages on Fridays."))
    s.close()

    decided = [(r["decision_point"], r["question_set"], r["action_taken"], r["mode"]) for r in rows(decision_log)]
    assert decided == [
        ("tool_gate", "tool_gate_start", "write", "shadow"),  # the turn wrote, though it searched first
        ("assess_effort", "assess_effort", "high", "shadow"),
        ("tool_gate", "tool_gate_next", "continue", "shadow"),
        ("assess_effort", "assess_effort", "high", "shadow"),
        ("note_type", "note_type", "model:none", "shadow"),  # the model gave no type for the note it wrote
        ("gate_write", "gate_write", "executed", "shadow"),
        ("tool_gate", "tool_gate_next", "answer", "shadow"),
        ("assess_effort", "assess_effort", "high", "shadow"),
        ("check_claim", "check_claim", "shown", "shadow"),
    ]
    first = rows(decision_log)[0]
    assert first["session_id"] == s.logger.session_id and first["step"] == 0
    assert first["proposed_action"] is not None


def test_the_gate_saw_the_id_the_search_returned(monkeypatch, decision_log):
    s = start(monkeypatch, A_WRITE_TURN, ShadowedTools(TOOLS, RESULTS))
    list(s.ask("Add a line to Priya's note"))
    s.close()

    gate = next(r for r in rows(decision_log) if r["decision_point"] == "gate_write")
    assert gate["status"] == "ok"  # passed the code checks: the ID came back from search_notes
    assert gate["outcome"] == "ok"
    claim = next(r for r in rows(decision_log) if r["decision_point"] == "check_claim")
    assert claim["status"] == "ok"  # the reply's note ID is a known one, so not a code failure


def test_the_execution_log_is_what_ran_with_note_text_left_out(monkeypatch, decision_log):
    results = {**RESULTS, "write_note": ToolResult("FileNotFoundError: no note", is_error=True)}
    s = start(monkeypatch, A_WRITE_TURN, ShadowedTools(TOOLS, results))
    list(s.ask("Add a line to Priya's note"))
    s.close()

    calls = rows(decision_log, "tool_calls")
    assert [(c["tool"], c["ok"]) for c in calls] == [("search", 1), ("write_note", 0)]
    assert "FileNotFoundError" in calls[1]["error"]
    assert "Prefers async" not in calls[1]["arguments_json"]
    assert NOTE_ID in calls[1]["arguments_json"]


def test_the_turn_is_identical_with_or_without_the_shadow(monkeypatch, decision_log):
    with_shadow = start(monkeypatch, A_WRITE_TURN, ShadowedTools(TOOLS, RESULTS))
    events_with = list(with_shadow.ask("Add a line to Priya's note"))
    with_shadow.close()

    without = start(monkeypatch, A_WRITE_TURN, ShadowedTools(TOOLS, RESULTS))
    without.shadow = None
    events_without = list(without.ask("Add a line to Priya's note"))
    without.close()

    assert events_with == events_without
    assert events_with[0] == ToolCall("search", {"query": "Priya"})
    assert events_with[-1] == Text(f"{NOTE_ID}.")


def test_a_dead_decision_layer_changes_nothing_and_the_execution_log_still_runs(monkeypatch, decision_log):
    tools = ShadowedTools(TOOLS, RESULTS, laya_down=True)
    s = start(monkeypatch, A_WRITE_TURN, tools)
    events = list(s.ask("Add a line to Priya's note"))
    s.close()

    assert events[-1] == Text(f"{NOTE_ID}.")
    assert [name for name, _ in tools.model_calls] == ["search", "write_note"]
    assert s.shadow.failures > 0
    assert rows(decision_log) == []
    assert len(rows(decision_log, "tool_calls")) == 2


def test_a_searchs_matches_by_meaning_are_reranked_in_shadow_and_exact_ones_are_not(monkeypatch, decision_log):
    found = {"query": "power budget", "matches": [
        {"path": "x.md", "id": None, "match": "exact", "snippet": "power budget"},
        {"path": "a.md", "id": None, "match": "meaning", "text": "power", "score": 0.8},
        {"path": "b.md", "id": None, "match": "meaning", "text": "budget", "score": 0.7}]}
    turn = [[{"tool_calls": [call("search", {"query": "power budget"})]}], ["The budget is 5 mA."]]
    s = start(monkeypatch, turn, ShadowedTools(TOOLS, {"search": json.dumps(found)}))
    list(s.ask("What's my power budget?"))
    s.close()

    reranked = [r for r in rows(decision_log) if r["decision_point"] == "rerank"]
    assert len(reranked) == 2
    assert {json.loads(r["state_text"])["passage"] for r in reranked} == {"power", "budget"}


def test_a_silent_call_is_marked_on_its_effort_decision(monkeypatch, decision_log):
    turn = [[{"thinking": "the answer is 42"}], ["42"]]
    s = start(monkeypatch, turn, ShadowedTools(TOOLS, {}))
    list(s.ask("What is six times seven?"))
    s.close()

    effort = next(r for r in rows(decision_log) if r["decision_point"] == "assess_effort")
    assert effort["action_taken"] == "high" and effort["outcome"] == "empty_reply"


def test_laya_sees_what_each_step_returned(monkeypatch, decision_log):
    s = start(monkeypatch, A_WRITE_TURN, ShadowedTools(TOOLS, RESULTS))
    list(s.ask("Add a line to Priya's note"))
    s.close()

    nexts = [json.loads(r["state_text"]) for r in rows(decision_log) if r["question_set"] == "tool_gate_next"]
    assert nexts[0]["steps"] == [{"tool": "search", "status": "ok", "result": f"1 found: Priya ({NOTE_ID})"}]
    # check_claim: Laya reads the reply alone; the log it is checked against is the harness's own.
    claim = next(json.loads(r["state_text"]) for r in rows(decision_log) if r["decision_point"] == "check_claim")
    assert claim == {"reply": f"Done - added it to {NOTE_ID}."}


def test_a_turn_without_tools_is_an_answer(monkeypatch, decision_log):
    s = start(monkeypatch, [["128.4 mAh."]], ShadowedTools(TOOLS, {}))
    list(s.ask("What's 5.35 mA times 24 hours?"))
    s.close()
    assert rows(decision_log)[0]["action_taken"] == "answer"


@pytest.mark.parametrize("tool, text, ok, error, summary", [
    ("search", json.dumps({"query": "x", "count": 5, "matches": [
        {"title": "A", "id": NOTE_ID}, {"title": "B", "id": None}, {"path": "c.md"}, {"title": "D"}]}), True, None,
     f"5 found: A ({NOTE_ID}), B, c.md and 2 more"),
    ("search", json.dumps({"query": "x", "matches": []}), True, None, "0 found"),
    ("write_note", json.dumps({"id": NOTE_ID, "path": "nuru/x.md"}), True, None, f"done: nuru/x.md {NOTE_ID}"),
    ("write_note", json.dumps({"path": "user/daily.md", "id": None, "created": True}), True, None,
     "done: user/daily.md (created)"),
    ("rename_or_move_file", json.dumps({"from": "nuru/x.md", "to": "archive/nuru/x.md"}), True, None,
     "moved nuru/x.md to archive/nuru/x.md"),
    ("write_note", "", False, "TimeoutError: the write timed out", "failed: TimeoutError: the write timed out"),
    ("read_note", "# Priya\nFirmware engineer.", True, None, "read 26 characters: # Priya Firmware engineer."),
    ("write_note", "nuru/priya-raman.md", True, None, "done: nuru/priya-raman.md"),
    ("multiply", "128.4", True, None, "128.4"),
])
def test_results_are_summarized_in_one_line(tool, text, ok, error, summary):
    assert summarize_result(tool, text, ok, error) == summary


def test_a_long_result_is_cut_to_one_short_line():
    summary = summarize_result("read_note", "word " * 500, True)
    assert len(summary) <= 160 and "\n" not in summary


def test_a_long_turn_keeps_the_state_bounded():
    shadow = DecisionShadow(lambda name, args: ToolResult("{}", is_error=True), None, "s")
    shadow._steps = [{"tool": f"t{i}", "status": "ok"} for i in range(10)]
    steps = shadow._recent_steps()
    assert steps[0] == {"earlier_steps": 4} and len(steps) == 7


@pytest.mark.parametrize("think, tier", [(False, "off"), (True, "high"), ("low", "low")])
def test_think_values_map_to_tier_names(think, tier):
    assert think_tier(think) == tier


def test_the_laya_server_is_started_without_a_vault_but_with_an_env_evals_can_extend():
    assert session.LAYA_SERVER_PARAMS.args[-1] == "laya-mcp"
    assert "VAULT_PATH" not in session.LAYA_SERVER_PARAMS.env  # it reads no notes since classify_notes went
    session.LAYA_SERVER_PARAMS.env.get("NURU_DECISION_CONFIG")  # drive_turn.py sets this key


@pytest.fixture
def laya_off_log(monkeypatch, tmp_path):
    """The shipped config (every Laya point off), logging to a temp database."""
    monkeypatch.setattr(session, "THINK", True)
    path = tmp_path / "decisions.sqlite3"
    config = load_config().model_copy(update={"database": path})
    assert all(config.mode(point) == "off" for point in LAYA_POINTS)
    monkeypatch.setattr(session, "load_decision_config", lambda: config)
    return path


def start_capturing_servers(monkeypatch, responses, tools):
    """start(), also returning the MCP servers the session asked for."""
    started = []
    real_session_start = Session.start
    monkeypatch.setattr(Session, "start", classmethod(lambda cls: None))
    start(monkeypatch, responses, tools)
    monkeypatch.setattr(session, "MCPTools", lambda server_params, **kwargs: started.extend(server_params) or tools)
    monkeypatch.setattr(Session, "start", real_session_start)
    return Session.start(), started


def test_with_every_laya_point_off_laya_is_neither_started_nor_asked(monkeypatch, laya_off_log):
    tools = ShadowedTools(TOOLS, RESULTS)
    s, started = start_capturing_servers(monkeypatch, A_WRITE_TURN, tools)
    events = list(s.ask("Add a line to Priya's note saying she prefers async messages on Fridays."))
    s.close()

    assert session.LAYA_SERVER_PARAMS not in started
    assert tools.harness_calls == []
    assert [name for name, _ in tools.model_calls] == ["search", "write_note"]
    assert events[-1] == Text(f"{NOTE_ID}.")
    assert rows(laya_off_log) == []  # nothing to log: no decision was asked, and code had no objection


def test_with_gate_write_off_write_checks_still_stop_a_bad_write(monkeypatch, laya_off_log):
    turn = [
        [{"tool_calls": [call("write_note", {"note": "user/plans", "content": "Draft"})]}],
        ["I couldn't write that."],
    ]
    tools = ShadowedTools(TOOLS, RESULTS)
    s, _ = start_capturing_servers(monkeypatch, turn, tools)
    list(s.ask("Write my plans into my own notes."))
    s.close()

    assert tools.model_calls == [] and tools.harness_calls == []
    [blocked] = rows(laya_off_log)
    assert (blocked["decision_point"], blocked["status"], blocked["mode"], blocked["proposed_action"]) == (
        "gate_write", "code_blocked", "enforce", "block")


def test_a_point_set_to_off_is_never_asked():
    asked = []
    shadow = DecisionShadow(lambda name, args: asked.append(name) or ToolResult("{}"), None, "s")
    shadow.off = set(LAYA_POINTS)
    assert shadow.watch("thinking...") is None
    shadow.begin("hi", None)
    assert asked == []
