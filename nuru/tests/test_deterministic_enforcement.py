"""End-to-end deterministic safeguards, with no predictor/server fixtures."""
import json
import pytest
from ulid import ULID
from core import session
from core.modelfile import render_modelfile
from core.decision_config import load_config
from core.decision_log import DecisionLog
from core.enforcement import Enforcement, log_lines
from core.expert import ExpertPlanner
from core.model_profile import ModelProfile
from core.providers.mcp_tools import ToolResult
from core.session import Session, Text, Thinking
PRIYA = str(ULID())
INVENTED = str(ULID())
REQUEST = "Add a line to Priya's note saying she prefers async messages on Fridays."
SEARCH = json.dumps({"matches": [{"path": "nuru/priya-raman.md", "id": PRIYA, "title": "Priya", "text": "Engineer"}]})
DEFAULT_SCRIPT = {}  # compatibility input for shared model-free fixtures
RUNAWAY = [{"thinking_chunk": f"Thought {i}: adding load {i}, recomputing the totals from the table once more. "} for i in range(900)]


def _expert_client(fail=False):
    from types import SimpleNamespace

    client = SimpleNamespace(calls=[])

    def create(**kwargs):
        client.calls.append(kwargs)
        if fail:
            return SimpleNamespace(stop_reason="refusal", content=[])
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="1. search c")])

    client.beta = SimpleNamespace(messages=SimpleNamespace(create=create))
    return client


def test_tier_escalation_is_deterministic_without_a_predictor(tmp_path):
    config = enforce_config(tmp_path, [], tiers={"enforce": True, "default": "low"})
    enforcement = Enforcement(config, {"off": False, "low": "low", "high": "high"}, None)
    policy = enforcement.new_task()
    assert enforcement.think_for(policy, False, True) == ("low", "low")
    assert enforcement.think_for(policy, True, True) == ("high", "high")
    from core.tier_policy import STOP
    assert enforcement.think_for(policy, True, True) == (STOP, STOP)


class Tools:
    """Model tool responses are faked; deterministic safeguards run normally."""

    retired, unavailable, cancel = None, (), None  # MCPTools' own state, read by Session

    def __init__(self, results):
        self.results = results
        self.calls = []

    def connect(self):
        pass

    def list_tools(self):
        return [{"type": "function", "function": {"name": n, "description": "x", "parameters": p}} for n, p in [
            ("read_note", {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
            ("search", {"properties": {"query": {"type": "string"}}, "required": ["query"], "type": "object"}),
            ("write_note", {"properties": {"content": {"type": "string"},
                                           "note": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}},
                            "required": ["content"], "type": "object"}),
        ]]

    def call_tool_result(self, name, arguments):
        self.calls.append((name, arguments))
        return ToolResult(self.results.get(name, "ok"))

    def call_harness_tool(self, name, arguments):
        if name == "note_info":  # the notes server's lookup: these tests' notes count as new ones
            return ToolResult(f"Error executing tool note_info: FileNotFoundError: No note found named '{arguments['name']}'.", is_error=True)
        raise AssertionError(f"Unexpected internal tool: {name}")

    def close(self):
        pass


def enforce_config(tmp_path, points, **overrides):
    """A test-only config: the given points enforced."""
    raw = load_config().model_dump(mode="json")
    raw["database"] = str(tmp_path / "decisions.sqlite3")
    raw["decision_points"] = {point: "shadow" for point in raw["decision_points"]}
    for point in points:
        raw["decision_points"][point] = "enforce"
    for key, value in overrides.items():
        raw[key] = {**raw[key], **value}
    return type(load_config()).model_validate(raw)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    sessions = []
    def make(points, responses, script=DEFAULT_SCRIPT, results=None, expert_client=None, **config_overrides):
        config = enforce_config(tmp_path, points, **config_overrides)
        monkeypatch.setattr(session, "THINK", True)
        # A model with real thinking levels, whatever the developer's .env names.
        monkeypatch.setattr(session, "PROFILE", ModelProfile({"off": False, "low": "low", "high": "high"}))
        monkeypatch.setattr(session, "load_decision_config", lambda: config)
        seen = []
        responses = iter(responses)

        def chat(model, messages, num_ctx=8192, think=True, tools=None, fmt=None):
            seen.append({"think": think, "tools": tools is not None, "fmt": fmt, "messages": [dict(m) for m in messages]})
            yield from next(responses)

        tools = Tools(results or {"search": SEARCH, "write_note": "nuru/priya-raman.md"})
        monkeypatch.setattr(session, "create_model", lambda model, base_model, modelfile_path: render_modelfile(modelfile_path, base_model))
        monkeypatch.setattr(session, "ensure_server_running", lambda: None)
        monkeypatch.setattr(session, "ensure_model_loaded", lambda model, *_: None)
        monkeypatch.setattr(session, "chat", chat)
        monkeypatch.setattr(session, "MCPTools", lambda server_params, **kwargs: tools)
        if expert_client is not None:
            monkeypatch.setattr(session, "ExpertPlanner",
                                lambda settings, log, **kwargs: ExpertPlanner(settings, log, expert_client))
        s = Session.start()
        sessions.append(s)
        return s, seen, tools

    yield make
    for s in sessions:
        s.close()


def call(name, arguments):
    return {"tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


def test_with_nothing_enforced_the_turn_is_the_models(setup):
    s, seen, tools = setup([], [["Hello."]])
    events = list(s.ask("hi"))
    assert events == [Text("Hello.")] and tools.calls == []
    assert seen[0]["think"] is True and seen[0]["tools"]


def test_at_the_step_cap_without_an_expert_the_model_answers_from_what_it_has_without_tools(setup):
    responses = [[call("search", {"query": q})] for q in "ab"] + [["Found a and b; c is still unchecked."]]
    s, seen, tools = setup(["step_cap"], responses, tool_gate={"step_cap": 2})
    events = list(s.ask("Find it"))
    assert events[-1] == Text("Found a and b; c is still unchecked.")
    assert seen[-1]["tools"] is True  # still listed, so Ollama's cached prompt holds; calls are refused
    assert seen[-1]["messages"][-1]["content"].startswith("[Nuru] That was the last tool round this turn (the 2-step limit)")


def test_a_tool_call_in_the_final_answer_is_refused_once_then_ends_the_turn(setup):
    s, seen, tools = setup(["step_cap"], [[call("search", {"query": q})] for q in "abcd"],
                           tool_gate={"step_cap": 2})
    events = list(s.ask("Find it"))
    assert isinstance(events[-1], Text) and events[-1].text.startswith("I stopped here: it reached the 2-step limit")
    assert [name for name, _ in tools.calls] == ["search", "search"]  # the third and fourth calls never ran
    assert "TOOLS_CLOSED" in seen[-1]["messages"][-1]["content"]  # the third was refused, then asked again


def test_after_a_refused_final_call_the_model_can_still_answer(setup):
    responses = [[call("search", {"query": q})] for q in "abc"] + [["Found a and b."]]
    s, seen, tools = setup(["step_cap"], responses, tool_gate={"step_cap": 2})
    events = list(s.ask("Find it"))
    assert events[-1] == Text("Found a and b.")
    assert len(tools.calls) == 2 and all(c["tools"] for c in seen)


def test_with_the_expert_off_at_the_cap_an_available_expert_is_not_asked(setup):
    client = _expert_client()
    responses = [[call("search", {"query": q})] for q in "ab"] + [["Here is what I found."]]
    s, seen, _ = setup(["step_cap"], responses, tool_gate={"step_cap": 2}, expert={"at_step_cap": False},
                       expert_client=client)
    events = list(s.ask("Find it"))
    assert client.calls == []
    assert events[-1] == Text("Here is what I found.")


def test_with_the_expert_on_at_the_cap_it_plans_first(setup):
    client = _expert_client()
    responses = [[call("search", {"query": q})] for q in "ab"] + [["Done with the plan."]]
    s, seen, _ = setup(["step_cap"], responses, tool_gate={"step_cap": 2}, expert={"at_step_cap": True},
                       expert_client=client)
    events = list(s.ask("Find it"))
    assert len(client.calls) == 1 and Thinking("Plan from the expert:\n1. search c") in events


def test_a_failed_expert_at_the_cap_still_ends_with_an_answer(setup):
    client = _expert_client(fail=True)
    responses = [[call("search", {"query": q})] for q in "ab"] + [["Partial answer; the rest is unresolved."]]
    s, seen, _ = setup(["step_cap"], responses, tool_gate={"step_cap": 2}, expert={"at_step_cap": True},
                       expert_client=client)
    events = list(s.ask("Find it"))
    assert events[-1] == Text("Partial answer; the rest is unresolved.")
    assert seen[-1]["tools"] is True


def test_log_lines_read_plainly():
    assert log_lines([]) == "- nothing was run"
    assert log_lines([{"tool": "write_note", "ok": False, "result": "failed: timeout"}]) == \
        "- write_note: FAILED - failed: timeout"


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


def test_step_limit_final_answer_remains_reachable_under_context_pressure(setup):
    s, seen, tools = setup(["step_cap"], [[call("search", {"query": "big"})],
                                        ["The omitted result is still unchecked."]],
                           results={"search": "x" * 100000}, tool_gate={"step_cap": 1})
    assert list(s.ask("Find it"))[-1] == Text("The omitted result is still unchecked.")
    assert len(seen) == 2 and seen[-1]["tools"] is True
    assert seen[-1]["messages"][-1]["content"].startswith("[Nuru] That was the last tool round")
    assert any(m.get("content", "").startswith("[left out:") for m in seen[-1]["messages"])


def test_at_the_step_cap_a_model_without_a_thinking_map_keeps_its_setting_and_asks_the_expert(tmp_path):
    """F13: no profile map to translate the expert tier, so the session's own thinking setting stands."""
    from core.tier_policy import TierPolicy

    class Available:
        def available(self):
            return True

    config = enforce_config(tmp_path, [], expert={"at_step_cap": True})
    enforcement = Enforcement(config, None, Available())
    assert enforcement.at_step_cap(TierPolicy(config.tiers.order), "high") == ("high", "expert")


def test_without_an_expert_there_is_no_plan(tmp_path):
    from core.expert import ExpertUnavailable
    enforcement = Enforcement(enforce_config(tmp_path, []), None, None)
    with pytest.raises(ExpertUnavailable, match="no expert is configured"):
        enforcement.expert_plan("Find it", [], [], [])


def _tiers_only(s):
    config = s.enforcement.config.model_copy(update={"decision_points": {p: "off" for p in s.enforcement.config.decision_points}})
    s.enforcement = Enforcement(config, session.PROFILE.think_map, s.enforcement.planner)


def test_tiers_alone_enforce_and_empty_reply_raises_thinking(setup):
    s, seen, _ = setup([], [[{"thinking": "the answer is 42"}], ["42"]], tiers={"enforce": True})
    _tiers_only(s)
    assert s.enforcement.any
    assert list(s.ask("six times seven"))[-1] == Text("42")
    assert [c["think"] for c in seen] == ["low", "high"]


def test_highest_tier_failure_without_expert_stops_with_reason(setup):
    s, seen, _ = setup([], [[{"thinking": "hmm"}]], tiers={"enforce": True, "default": "high"})
    _tiers_only(s)
    s.enforcement.planner = None
    events = list(s.ask("Something hard"))
    assert len(seen) == 1
    assert events[-1].text.startswith("I stopped here: the local model kept failing at the highest thinking level")
    assert ", and the expert isn't available (no API key, or today's budget is used up)" in events[-1].text


def test_past_highest_tier_expert_plans_and_model_continues(setup):
    client = _expert_client()
    s, seen, _ = setup([], [[{"thinking": "hmm"}], ["Done."]], tiers={"enforce": True, "default": "high"}, expert_client=client)
    _tiers_only(s)
    events = list(s.ask("Something hard"))
    assert len(client.calls) == 1
    assert Thinking("Plan from the expert:\n1. search c") in events
    assert events[-1] == Text("Done.")
    assert seen[-1]["messages"][-1]["content"].startswith("[Nuru] A plan from the expert")


def test_expert_failure_past_highest_tier_stops_instead_of_continuing(setup):
    client = _expert_client(fail=True)
    s, seen, _ = setup([], [[{"thinking": "hmm"}], ["must not run"]], tiers={"enforce": True, "default": "high"}, expert_client=client)
    _tiers_only(s)
    events = list(s.ask("Something hard"))
    assert len(seen) == 1 and len(client.calls) == 1
    assert events[-1].text.startswith("I stopped here: it needed the expert, and the expert couldn't help:")


def test_the_turn_is_identical_with_or_without_the_audit_shadow(setup):
    responses = [[call("search", {"query": "Priya"})], ["Found Priya."]]
    s, seen, tools = setup([], responses)
    before = list(s.ask("Find Priya")), seen[:], tools.calls[:]
    s2, seen2, tools2 = setup([], responses)
    s2.shadow.close()
    s2.shadow = None
    after = list(s2.ask("Find Priya")), seen2[:], tools2.calls[:]
    assert before == after


@pytest.mark.parametrize("ending", ["recovery", "step_cap"])
def test_final_answer_cannot_loop_on_reasoning_only_with_tiers_enforced(setup, monkeypatch, ending):
    rounds = ([call("read_note", {"name": 1}), call("search", {"query": "a"}),
               call("search", {"query": "b"})] if ending == "recovery" else
              [call("search", {"query": q}) for q in "ab"])
    points = ["step_cap"] if ending == "step_cap" else []
    s, seen, tools = setup(points, [], tool_gate={"step_cap": 2}, tiers={"enforce": True})

    def chat(model, messages, **kwargs):
        seen.append({"think": kwargs["think"], "tools": kwargs["tools"]})
        assert len(seen) <= len(rounds) + 1, "reasoning-only final answer looped"
        if len(seen) <= len(rounds):
            yield rounds[len(seen) - 1]
        else:
            # Even a model ignoring think=False must terminate without another call.
            yield {"thinking": "The available evidence remains incomplete."}

    monkeypatch.setattr(session, "chat", chat)
    events = list(s.ask("Find it"))
    assert isinstance(events[-1], Text)
    assert len(seen) == len(rounds) + 1
    assert seen[0]["think"] == "low"
    assert seen[-1]["think"] is False and seen[-1]["tools"] == seen[0]["tools"]
    assert len(tools.calls) == 2
