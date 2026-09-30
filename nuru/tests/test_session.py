import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from core import session
from core.conversation_store import ConversationStore
from core.providers.mcp_tools import ToolResult
from core.session import Failure, RetryWithoutThinking, Session, Text, Thinking, ToolCall


def said(events):
    """A turn's events without the status ones (ToolResult, Progress, LayaDecision) - what it says."""
    from core.session import STATUS_EVENTS

    return [e for e in events if not isinstance(e, STATUS_EVENTS)]


@pytest.fixture(autouse=True)
def _isolate_log_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "LOG_DIR", tmp_path)
    monkeypatch.setattr(session, "DB_PATH", tmp_path / "conversation.db")
    # Building the custom Ollama model is a real `ollama create` subprocess
    # call - never let it run for real in a test.
    monkeypatch.setattr(session, "create_model", lambda model, base_model, modelfile_path: None)
    # THINK comes from the developer's own .env via load_dotenv, so without this the
    # thinking-mode tests pass or fail depending on what the machine is set up to run.
    monkeypatch.setattr(session, "THINK", True)


def fake_chat(chunks):
    def _chat(model, messages, num_ctx=8192, think=True, tools=None):
        yield from chunks

    return _chat


class FakeMCPTools:
    """Stand-in for MCPTools that skips actually spawning the MCP server."""

    def __init__(self, tools=(), tool_results=None):
        self._tools = list(tools)
        self._tool_results = tool_results or {}
        self.calls = []
        self.connected = False
        self.closed = False

    def connect(self):
        self.connected = True

    def list_tools(self):
        return self._tools

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self._tool_results.get(name, "")

    def call_tool_result(self, name, arguments):
        return ToolResult(self.call_tool(name, arguments))

    def call_harness_tool(self, name, arguments):
        # No decision layer here: the shadow's calls fail, which it must shrug off.
        return ToolResult("no decision layer in this test", is_error=True)

    def close(self):
        self.closed = True


def start_session(monkeypatch, chat=None, tools=None, model_info=None):
    """A started Session with everything that would touch Ollama or spawn a server faked out."""
    monkeypatch.setattr(session, "ensure_server_running", lambda: None)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model: model_info)
    monkeypatch.setattr(session, "chat", chat or fake_chat([]))
    fake_tools = tools or FakeMCPTools()
    monkeypatch.setattr(session, "MCPTools", lambda server_params, **kwargs: fake_tools)
    return Session.start()


def logged_events(log_dir):
    (log_file,) = log_dir.glob("*.jsonl")
    return [json.loads(line)["event"] for line in log_file.read_text().splitlines()]


def tool_call(name, arguments):
    return {"function": {"name": name, "arguments": arguments}}


def test_start_builds_the_custom_model_from_the_modelfile_before_loading(monkeypatch):
    calls = []
    monkeypatch.setattr(
        session, "create_model", lambda model, base_model, modelfile_path: calls.append((model, base_model, modelfile_path))
    )

    start_session(monkeypatch)

    assert calls == [(session.MODEL, session.BASE_MODEL, session._modelfile_for(session.BASE_MODEL))]


# --- _modelfile_for ---


def test_modelfile_for_picks_the_file_named_after_the_model_family(monkeypatch, tmp_path):
    (tmp_path / "qwen3.5.Modelfile").write_text("FROM __BASE_MODEL__\n")
    monkeypatch.setattr(session, "MODELFILES_DIR", tmp_path)

    assert session._modelfile_for("qwen3.5:9b") == tmp_path / "qwen3.5.Modelfile"


def test_modelfile_for_strips_the_tag_and_any_registry_namespace(monkeypatch, tmp_path):
    (tmp_path / "qwen3.5.Modelfile").write_text("FROM __BASE_MODEL__\n")
    monkeypatch.setattr(session, "MODELFILES_DIR", tmp_path)

    expected = tmp_path / "qwen3.5.Modelfile"
    assert session._modelfile_for("qwen3.5") == expected
    assert session._modelfile_for("library/qwen3.5:latest") == expected


def test_modelfile_for_distinguishes_families_that_share_a_prefix(monkeypatch, tmp_path):
    (tmp_path / "qwen3.Modelfile").write_text("FROM __BASE_MODEL__\n")
    (tmp_path / "qwen3.5.Modelfile").write_text("FROM __BASE_MODEL__\n")
    monkeypatch.setattr(session, "MODELFILES_DIR", tmp_path)

    assert session._modelfile_for("qwen3:8b") == tmp_path / "qwen3.Modelfile"
    assert session._modelfile_for("qwen3.5:9b") == tmp_path / "qwen3.5.Modelfile"


def test_modelfile_for_raises_naming_the_available_families(monkeypatch, tmp_path):
    (tmp_path / "qwen3.5.Modelfile").write_text("FROM __BASE_MODEL__\n")
    (tmp_path / "llama3.1.Modelfile").write_text("FROM __BASE_MODEL__\n")
    monkeypatch.setattr(session, "MODELFILES_DIR", tmp_path)

    with pytest.raises(FileNotFoundError, match="phi4"):
        session._modelfile_for("phi4:14b")

    with pytest.raises(FileNotFoundError, match="llama3.1, qwen3.5"):
        session._modelfile_for("phi4:14b")


def test_every_shipped_modelfile_has_the_base_model_placeholder():
    """create_model() builds whatever FROM line it finds, so a copy-paste slip that
    drops the placeholder would silently build every model from the same base."""
    shipped = sorted(session.MODELFILES_DIR.glob("*.Modelfile"))

    assert shipped, f"no Modelfiles in {session.MODELFILES_DIR}"
    for path in shipped:
        text = path.read_text()
        assert text.startswith("FROM __BASE_MODEL__"), path.name
        # A second FROM silently wins over the rendered one, so NURU_BASE_MODEL would be
        # ignored and every base model would build from whatever that line names.
        froms = [ln for ln in text.splitlines() if ln.startswith("FROM ")]
        assert froms == ["FROM __BASE_MODEL__"], f"{path.name} has {len(froms)} FROM lines: {froms}"


def test_start_connects_mcp_tools_and_passes_them_to_chat(monkeypatch):
    tools = [{"type": "function", "function": {"name": "get_temperature"}}]
    fake_tools = FakeMCPTools(tools=tools)
    seen = {}

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        seen["tools"] = tools
        return iter([])

    s = start_session(monkeypatch, chat=recording_chat, tools=fake_tools)
    said(s.ask("hi"))

    assert fake_tools.connected is True
    assert seen["tools"] == tools


def test_start_offers_the_model_only_its_allowlist(monkeypatch):
    seen = {}

    def recording_mcp_tools(server_params, **kwargs):
        seen.update(kwargs)
        return FakeMCPTools()

    monkeypatch.setattr(session, "ensure_server_running", lambda: None)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model: None)
    monkeypatch.setattr(session, "MCPTools", recording_mcp_tools)

    Session.start()

    assert "delete_file_or_directory" not in seen["model_tools"]
    assert "archive_note" in seen["model_tools"] and "classify_notes" in seen["model_tools"]
    for harness_only in ("tool_gate", "assess_effort", "gate_write", "check_claim", "rerank", "health"):
        assert harness_only not in seen["model_tools"]


def test_start_closes_what_it_opened_when_a_later_step_fails(monkeypatch):
    fake_tools = FakeMCPTools()
    monkeypatch.setattr(session, "ensure_server_running", lambda: None)
    monkeypatch.setattr(session, "MCPTools", lambda server_params, **kwargs: fake_tools)

    def failing_load(model):
        raise RuntimeError("model would not load")

    monkeypatch.setattr(session, "ensure_model_loaded", failing_load)

    with pytest.raises(RuntimeError, match="model would not load"):
        Session.start()

    assert fake_tools.closed is True


def test_close_releases_the_tools_and_the_database_without_touching_ollama(monkeypatch):
    fake_tools = FakeMCPTools()
    s = start_session(monkeypatch, tools=fake_tools)

    def unexpected(*a, **k):
        raise AssertionError("close must leave the model and the server alone")

    monkeypatch.setattr(session, "unload_all_models", unexpected)
    monkeypatch.setattr(session, "stop_server", unexpected)

    s.close()

    assert fake_tools.closed is True
    with pytest.raises(Exception, match="closed database"):
        s.store.get_messages(s.conversation_id)


def test_shutdown_unloads_models_stops_the_server_and_logs_why(monkeypatch, tmp_path):
    s = start_session(monkeypatch)
    calls = []
    monkeypatch.setattr(session, "unload_all_models", lambda: calls.append("unload"))
    monkeypatch.setattr(session, "stop_server", lambda: calls.append("stop"))

    s.shutdown("/exit")

    assert calls == ["unload", "stop"]
    assert logged_events(tmp_path)[-1] == "session_end"


def test_load_model_starts_the_server_and_loads_the_model(monkeypatch):
    s = start_session(monkeypatch)
    calls = []
    monkeypatch.setattr(session, "ensure_server_running", lambda: calls.append("server") or True)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model: calls.append(("model", model)) or {})

    s.load_model()

    assert calls == ["server", ("model", session.MODEL)]


def test_loaded_model_info_returns_the_info_when_the_model_is_loaded(monkeypatch):
    s = start_session(monkeypatch)
    monkeypatch.setattr(session, "is_model_loaded", lambda model: True)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model: {"family": "qwen3"})

    assert s.loaded_model_info() == {"family": "qwen3"}


def test_loaded_model_info_is_none_and_loads_nothing_when_the_model_is_not_loaded(monkeypatch):
    s = start_session(monkeypatch)
    monkeypatch.setattr(session, "is_model_loaded", lambda model: False)

    def unexpected(model):
        raise AssertionError("asking for the info must not load the model")

    monkeypatch.setattr(session, "ensure_model_loaded", unexpected)

    assert s.loaded_model_info() is None


def test_ask_yields_the_reply_as_text_events_and_saves_it(monkeypatch):
    s = start_session(
        monkeypatch, chat=fake_chat(["Hi", " there", {"prompt_tokens": 1, "completion_tokens": 2}])
    )

    events = said(s.ask("hello"))

    assert events == [Text("Hi"), Text(" there")]
    saved = s.store.get_messages(s.conversation_id)
    assert [(m["role"], m["content"], m["tokens_used"]) for m in saved] == [("user", "hello", 1), ("nuru", "Hi there", 2)]


def test_ask_retries_with_thinking_off_when_the_model_only_thinks(monkeypatch):
    think_per_call = []

    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        think_per_call.append(think)
        if think:  # the whole answer goes into the reasoning and the turn ends with no reply
            yield {"thinking": "  The answer is 4.\n"}
            yield {"prompt_tokens": 1, "completion_tokens": 2}
        else:
            yield "4"
            yield {"prompt_tokens": 1, "completion_tokens": 1}

    s = start_session(monkeypatch, chat=chat)

    events = said(s.ask("what is 2+2?"))

    assert events == [Thinking("The answer is 4."), RetryWithoutThinking(), Text("4")]
    assert think_per_call == [True, False]
    saved = s.store.get_messages(s.conversation_id)  # what the retry wrote is the reply of record
    assert [(m["role"], m["content"]) for m in saved] == [("user", "what is 2+2?"), ("nuru", "4")]


def test_ask_says_no_reply_was_written_when_the_retry_stays_silent_too(monkeypatch):
    # fake_chat ignores `think`, so the retry comes back just as silent as the first call.
    s = start_session(
        monkeypatch,
        chat=fake_chat([{"thinking": "  The answer is 4.\n"}, {"prompt_tokens": 1, "completion_tokens": 2}]),
    )

    events = said(s.ask("what is 2+2?"))

    assert events == [
        Thinking("The answer is 4."),
        RetryWithoutThinking(),  # and only the one: a turn is never retried twice
        Thinking("The answer is 4."),
        Text(session._NO_REPLY_NOTE),
    ]
    saved = s.store.get_messages(s.conversation_id)  # the reasoning is still what the model said
    assert [(m["role"], m["content"]) for m in saved] == [("user", "what is 2+2?"), ("nuru", "The answer is 4.")]


def test_ask_does_not_retry_when_thinking_was_off_to_begin_with(monkeypatch):
    monkeypatch.setattr(session, "THINK", False)
    think_per_call = []

    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        think_per_call.append(think)
        yield {"prompt_tokens": 1, "completion_tokens": 0}

    s = start_session(monkeypatch, chat=chat)

    assert said(s.ask("hello")) == []
    assert think_per_call == [False]  # nothing was routed into reasoning, so there is nothing to retry


def test_ask_shows_the_thinking_alongside_a_reply_the_model_did_write(monkeypatch):
    s = start_session(
        monkeypatch, chat=fake_chat(["4", {"thinking": "2+2"}, {"prompt_tokens": 1, "completion_tokens": 2}])
    )

    assert said(s.ask("what is 2+2?")) == [Text("4"), Thinking("2+2")]


def test_ask_shows_the_thinking_that_comes_with_a_tool_call(monkeypatch):
    responses = iter(
        [
            [
                {"thinking": "I should look this up"},
                {"tool_calls": [tool_call("get_temperature", {"city": "Berlin"})]},
                {"prompt_tokens": 1, "completion_tokens": 1},
            ],
            ["It's 17C", {"prompt_tokens": 2, "completion_tokens": 2}],
        ]
    )
    s = start_session(
        monkeypatch,
        chat=lambda *a, **k: next(responses),
        tools=FakeMCPTools(tool_results={"get_temperature": "17C"}),
    )

    events = said(s.ask("what's the weather in Berlin?"))

    assert events == [
        Thinking("I should look this up"),
        ToolCall("get_temperature", {"city": "Berlin"}),
        Text("It's 17C"),
    ]


def test_ask_says_nothing_when_the_model_writes_neither_a_reply_nor_thinking(monkeypatch):
    s = start_session(monkeypatch, chat=fake_chat([{"prompt_tokens": 1, "completion_tokens": 0}]))

    assert said(s.ask("hello")) == []


def test_ask_checks_the_server_and_the_model_before_each_send(monkeypatch):
    calls = []
    monkeypatch.setattr(session, "ensure_server_running", lambda: calls.append("server"))
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model: calls.append(("model", model)))
    monkeypatch.setattr(session, "chat", fake_chat([]))
    monkeypatch.setattr(session, "MCPTools", lambda server_params, **kwargs: FakeMCPTools())

    s = Session.start()
    said(s.ask("hi"))

    assert calls == ["server", ("model", session.MODEL), "server", ("model", session.MODEL)]


def test_ask_includes_prior_turns_from_the_same_conversation_as_history(monkeypatch):
    monkeypatch.setattr(session, "_timestamp_line", lambda now: "[NOW]")
    sent = []

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        sent.append(messages)
        return iter([])

    s = start_session(monkeypatch, chat=recording_chat)
    said(s.ask("first"))
    said(s.ask("second"))

    assert sent[0] == [{"role": "user", "content": "[NOW]\n\nfirst"}]
    assert sent[1] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "[NOW]\n\nsecond"},
    ]


def test_timestamp_line_gives_the_weekday_date_time_and_zone():
    now = datetime(2026, 9, 21, 17, 52, tzinfo=timezone(timedelta(hours=2), "CEST"))

    assert session._timestamp_line(now) == "[Current date and time: Monday 2026-09-21 17:52 CEST]"


def test_ask_sends_the_current_date_and_time_ahead_of_the_message_but_saves_it_as_typed(monkeypatch):
    monkeypatch.setattr(session, "_timestamp_line", lambda now: "[NOW]")
    sent = []

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        sent.append([dict(m) for m in messages])
        return iter([])

    s = start_session(monkeypatch, chat=recording_chat)
    said(s.ask("what day is it?"))

    assert sent[0] == [{"role": "user", "content": "[NOW]\n\nwhat day is it?"}]
    saved = s.store.get_messages(s.conversation_id)
    assert [(m["role"], m["content"]) for m in saved] == [("user", "what day is it?"), ("nuru", "")]


def test_ask_keeps_the_date_and_time_ahead_of_the_message_through_a_tool_round(monkeypatch):
    monkeypatch.setattr(session, "_timestamp_line", lambda now: "[NOW]")
    responses = iter([[{"tool_calls": [tool_call("get_temperature", {})]}], []])
    sent = []

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        sent.append([dict(m) for m in messages])
        return iter(next(responses))

    s = start_session(
        monkeypatch, chat=recording_chat, tools=FakeMCPTools(tool_results={"get_temperature": "17C"})
    )
    said(s.ask("weather?"))

    assert sent[0][0] == {"role": "user", "content": "[NOW]\n\nweather?"}
    assert sent[1][0] == {"role": "user", "content": "[NOW]\n\nweather?"}


def test_ask_passes_the_think_setting_to_chat(monkeypatch):
    monkeypatch.setattr(session, "THINK", False)
    seen = {}

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        seen["think"] = think
        return iter([])

    list(start_session(monkeypatch, chat=recording_chat).ask("hi"))

    assert seen["think"] is False


def test_ask_requests_the_capped_context_window_from_chat(monkeypatch):
    seen = {}

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        seen["num_ctx"] = num_ctx
        return iter([])

    list(start_session(monkeypatch, chat=recording_chat, model_info={"context_window": 262144}).ask("hi"))

    assert seen["num_ctx"] == session.MAX_NUM_CTX


def test_ask_runs_a_tool_call_and_sends_the_result_back_to_the_model(monkeypatch):
    fake_tools = FakeMCPTools(tool_results={"get_temperature": "17C"})
    responses = iter(
        [
            [{"tool_calls": [tool_call("get_temperature", {"city": "Berlin"})]}, {"prompt_tokens": 1, "completion_tokens": 1}],
            ["It's 17C", {"prompt_tokens": 2, "completion_tokens": 2}],
        ]
    )
    sent = []

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        sent.append([dict(m) for m in messages])
        yield from next(responses)

    s = start_session(monkeypatch, chat=recording_chat, tools=fake_tools)

    events = said(s.ask("what's the weather in Berlin?"))

    assert events == [ToolCall("get_temperature", {"city": "Berlin"}), Text("It's 17C")]
    assert fake_tools.calls == [("get_temperature", {"city": "Berlin"})]
    assert sent[1][-1] == {"role": "tool", "content": "17C"}


def test_ask_yields_the_tool_call_before_running_it(monkeypatch):
    fake_tools = FakeMCPTools()
    responses = iter([[{"tool_calls": [tool_call("slow_tool", {})]}], []])
    s = start_session(monkeypatch, chat=lambda *a, **k: iter(next(responses)), tools=fake_tools)

    turn = s.ask("go")
    event = next(e for e in turn if not isinstance(e, session.STATUS_EVENTS))

    assert event == ToolCall("slow_tool", {})
    assert fake_tools.calls == []  # a UI gets to show the call while the tool is still running
    list(turn)
    assert fake_tools.calls == [("slow_tool", {})]


def test_ask_accepts_tool_arguments_sent_as_a_json_string(monkeypatch):
    fake_tools = FakeMCPTools()
    responses = iter([[{"tool_calls": [tool_call("get_temperature", '{"city": "Berlin"}')]}], []])
    s = start_session(monkeypatch, chat=lambda *a, **k: iter(next(responses)), tools=fake_tools)

    events = said(s.ask("weather?"))

    assert events == [ToolCall("get_temperature", {"city": "Berlin"})]
    assert fake_tools.calls == [("get_temperature", {"city": "Berlin"})]


def test_ask_fails_and_forgets_the_message_when_the_request_fails(monkeypatch):
    def failing_chat(model, messages, num_ctx=8192, think=True, tools=None):
        raise session.requests.exceptions.ReadTimeout("timed out")
        yield  # pragma: no cover - makes this a generator

    s = start_session(monkeypatch, chat=failing_chat)

    first = said(s.ask("hello"))
    second = said(s.ask("still here?"))  # the session keeps working after a failure

    for events in (first, second):
        assert len(events) == 1
        assert isinstance(events[0], Failure)
        assert "request to the model failed: timed out" in events[0].message
        assert "Your message was not saved" in events[0].message
    assert s.store.get_messages(s.conversation_id) == []


def test_ask_rejects_a_message_that_would_exceed_the_token_budget(monkeypatch):
    def unexpected_chat(*a, **k):
        raise AssertionError("chat should not be called for an over-budget message")

    s = start_session(monkeypatch, chat=unexpected_chat, model_info={"context_window": 1000})

    events = said(s.ask("x" * 100_000))

    assert len(events) == 1
    assert isinstance(events[0], Failure)
    assert events[0].message.startswith("message too long")
    assert s.store.get_messages(s.conversation_id) == []


def test_ask_accepts_a_message_within_the_token_budget(monkeypatch):
    s = start_session(
        monkeypatch,
        chat=fake_chat(["ok", {"prompt_tokens": 1, "completion_tokens": 1}]),
        model_info={"context_window": 1000},
    )

    assert said(s.ask("hi")) == [Text("ok")]


def test_ask_stops_when_cancelled_and_keeps_what_was_said_so_far(monkeypatch, tmp_path):
    cancel = threading.Event()

    def chat_that_gets_stopped(model, messages, num_ctx=8192, think=True, tools=None):
        yield "Hel"
        cancel.set()  # the user hits stop while the model is still talking
        yield "lo"
        yield {"prompt_tokens": 1, "completion_tokens": 2}

    s = start_session(monkeypatch, chat=chat_that_gets_stopped)

    events = said(s.ask("hi", cancel))

    assert events == [Text("Hel")]
    saved = s.store.get_messages(s.conversation_id)
    assert [(m["role"], m["content"]) for m in saved] == [("user", "hi"), ("nuru", "Hel")]
    assert logged_events(tmp_path)[-1] == "turn_end"


def test_ask_runs_no_more_tools_once_cancelled(monkeypatch):
    cancel = threading.Event()

    class StoppingTools(FakeMCPTools):
        def call_tool(self, name, arguments):
            cancel.set()  # stop pressed while the first tool is running
            return super().call_tool(name, arguments)

    fake_tools = StoppingTools()
    calls = [tool_call("first", {}), tool_call("second", {})]
    chats = []

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        chats.append(messages)
        yield {"tool_calls": calls}

    s = start_session(monkeypatch, chat=recording_chat, tools=fake_tools)

    events = said(s.ask("go", cancel))

    assert [e.name for e in events if isinstance(e, ToolCall)] == ["first"]
    assert fake_tools.calls == [("first", {})]
    assert len(chats) == 1  # and the model wasn't asked to continue


def test_estimate_tokens_uses_chars_per_token_heuristic():
    assert session._estimate_tokens("a" * 400) == 100


def test_token_budget_reserves_the_response_fraction():
    assert session._token_budget(1000) == 800


def test_context_window_is_capped_at_max_num_ctx():
    assert session._context_window({"context_window": 262144}) == session.MAX_NUM_CTX


def test_context_window_passes_a_smaller_model_window_through_unchanged():
    assert session._context_window({"context_window": 4096}) == 4096


def test_context_window_falls_back_to_the_default_when_model_info_is_missing():
    assert session._context_window(None) == session.DEFAULT_NUM_CTX
    assert session._context_window({}) == session.DEFAULT_NUM_CTX


def test_the_store_can_be_used_from_another_thread(tmp_path):
    # The UI starts the session on one thread and runs each turn on another.
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    errors = []

    def use_store():
        try:
            store.add_message(conversation_id, "user", "hi")
        except Exception as e:  # pragma: no cover - only reached if the store refuses
            errors.append(e)

    worker = threading.Thread(target=use_store)
    worker.start()
    worker.join()

    assert errors == []


# --- _parse_think ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("medium", "medium"),   # a level goes to the API as-is
        ("low", "low"),
        ("high", "high"),
        ("MEDIUM", "medium"),
        ("false", False),
        ("true", True),
        (" medium ", "medium"),
        ("anything-else", True),
    ],
)
def test_parse_think_accepts_levels_and_the_old_booleans(raw, expected):
    assert session._parse_think(raw) == expected


def test_a_reasoning_level_still_triggers_the_silent_turn_retry(monkeypatch):
    """The retry checks `think` for truthiness, so a level must not bypass it."""
    monkeypatch.setattr(session, "THINK", "medium")
    seen = []

    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        seen.append(think)
        if think:  # silent turn: everything went into the reasoning
            yield {"thinking": "the answer is 4"}
            yield {"prompt_tokens": 1, "completion_tokens": 1}
        else:
            yield "4"
            yield {"prompt_tokens": 1, "completion_tokens": 1}

    s = start_session(monkeypatch, chat=chat)
    events = said(s.ask("what is 2+2?"))

    assert seen == ["medium", False]
    assert events == [Thinking("the answer is 4"), RetryWithoutThinking(), Text("4")]


class FakeIndexer:
    def __init__(self):
        self.pending = 0
        self.changes = 0
        self.last_change = ""
        self.on_change = None
        self.calls = []

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")


def test_start_runs_the_background_indexer_and_close_stops_it(monkeypatch):
    indexer = FakeIndexer()
    monkeypatch.setattr(session, "VaultIndexer", type("V", (), {"from_env": staticmethod(lambda: indexer)}))
    s = start_session(monkeypatch)

    assert indexer.calls == ["start"]
    assert s.indexing == 0
    assert callable(indexer.on_change)  # wired to the session log
    indexer.pending = 7
    assert s.indexing == 7

    s.close()

    assert indexer.calls == ["start", "stop"]


def test_a_session_without_a_vault_has_no_indexer(monkeypatch):
    s = start_session(monkeypatch)

    assert s.indexer is None
    assert s.indexing == 0
    s.close()


def context_overflow_error():
    """What chat() raises when Ollama rejects a request longer than num_ctx."""
    response = session.requests.Response()
    response.status_code = 400
    response._content = (
        b'{"error":"request (23401 tokens) exceeds the available context size (16384 tokens), try increasing it"}'
    )
    return session.requests.exceptions.HTTPError("400 Client Error: Bad Request", response=response)


def test_a_tool_result_too_long_for_the_context_is_swapped_for_a_note_and_the_turn_goes_on(monkeypatch):
    fake_tools = FakeMCPTools(tool_results={"ask_an_expert": "x" * 50_000})
    seen = []
    calls = iter(["tool", "overflow", "answer"])

    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        seen.append([dict(m) for m in messages])
        step = next(calls)
        if step == "tool":
            yield {"tool_calls": [tool_call("ask_an_expert", {"message": "audit"})]}
        elif step == "overflow":
            raise context_overflow_error()
        else:
            yield "Here is what I found."
            yield {"prompt_tokens": 10, "completion_tokens": 5}

    s = start_session(monkeypatch, chat=chat, tools=fake_tools)

    events = said(s.ask("audit my notes"))

    assert events == [ToolCall("ask_an_expert", {"message": "audit"}), Text("Here is what I found.")]
    assert seen[1][-1]["content"] == "x" * 50_000  # the request Ollama refused
    note = seen[2][-1]["content"]
    assert note.startswith("[The ask_an_expert result was about")
    assert "too long for the context window" in note
    assert s.store.get_messages(s.conversation_id)[-1]["content"] == "Here is what I found."


def test_a_second_overflow_in_the_same_turn_ends_it_as_before(monkeypatch):
    fake_tools = FakeMCPTools(tool_results={"ask_an_expert": "x" * 50_000})
    calls = iter(["tool", "overflow", "overflow"])

    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        if next(calls) == "tool":
            yield {"tool_calls": [tool_call("ask_an_expert", {"message": "audit"})]}
        else:
            raise context_overflow_error()

    s = start_session(monkeypatch, chat=chat, tools=fake_tools)

    events = said(s.ask("audit my notes"))

    assert isinstance(events[-1], Failure)
    assert "request to the model failed" in events[-1].message


def test_an_overflow_with_no_tool_result_to_cut_ends_the_turn_as_before(monkeypatch):
    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        raise context_overflow_error()
        yield  # pragma: no cover - makes this a generator

    s = start_session(monkeypatch, chat=chat)

    events = said(s.ask("hello"))

    assert len(events) == 1 and isinstance(events[0], Failure)
    assert s.store.get_messages(s.conversation_id) == []


EXPERT_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_an_expert",
        "description": "",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "best_guess": {"type": "string"},
                "file_paths": {"type": "array"},
                "user_request": {"type": "string"},
            },
            "required": ["question"],
        },
    },
}
EXPERT_ANSWER = json.dumps({"answer": "Architecture B passes.", "stop_reason": "end_turn"})


def scripted_chat(steps, seen=None):
    """A chat() that plays one step per call: a list of tool calls to make, or reply text."""
    steps = iter(steps)

    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        if seen is not None:
            seen.append([dict(m) for m in messages])
        step = next(steps)
        if isinstance(step, list):
            yield {"tool_calls": step}
        else:
            yield step
            yield {"prompt_tokens": 1, "completion_tokens": 1}

    return chat


def test_the_model_is_not_asked_for_the_user_request_the_session_fills_in(monkeypatch):
    s = start_session(monkeypatch, tools=FakeMCPTools(tools=[EXPERT_TOOL]))

    parameters = s.tool_definitions[0]["function"]["parameters"]
    assert set(parameters["properties"]) == {"question", "best_guess", "file_paths"}
    assert parameters["required"] == ["question"]


def test_ask_an_expert_gets_the_users_message_verbatim(monkeypatch):
    fake_tools = FakeMCPTools(tools=[EXPERT_TOOL], tool_results={"ask_an_expert": EXPERT_ANSWER})
    chat = scripted_chat([[tool_call("ask_an_expert", {"question": "Is B viable?"})], "B passes."])
    s = start_session(monkeypatch, chat=chat, tools=fake_tools)

    said(s.ask("Audit my battery notes, line by line."))

    assert fake_tools.calls == [
        ("ask_an_expert", {"question": "Is B viable?", "user_request": "Audit my battery notes, line by line."})
    ]


def test_after_two_expert_answers_the_tool_is_withdrawn_and_the_model_told_to_ask_the_user(monkeypatch):
    fake_tools = FakeMCPTools(tools=[EXPERT_TOOL], tool_results={"ask_an_expert": EXPERT_ANSWER})
    offered = []
    steps = iter([
        [tool_call("ask_an_expert", {"question": "Is B viable?"})],
        [tool_call("ask_an_expert", {"question": "And under PSM?"})],
        "The expert says architecture B passes.",
    ])

    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        offered.append([t["function"]["name"] for t in tools or []])
        offered_messages.append([dict(m) for m in messages])
        step = next(steps)
        yield {"tool_calls": step} if isinstance(step, list) else step

    offered_messages = []
    s = start_session(monkeypatch, chat=chat, tools=fake_tools)

    events = said(s.ask("Audit my battery notes."))

    assert len(fake_tools.calls) == 2
    assert offered == [["ask_an_expert"], ["ask_an_expert"], []]  # gone after the second answer
    assert offered_messages[2][-1]["content"].endswith(session._EXPERT_LAST_ANSWER)
    assert events[-1] == Text("The expert says architecture B passes.")


def test_an_expert_call_past_the_limit_ends_the_turn_asking_the_user(monkeypatch):
    fake_tools = FakeMCPTools(tools=[EXPERT_TOOL], tool_results={"ask_an_expert": EXPERT_ANSWER})
    chat = scripted_chat([[tool_call("ask_an_expert", {"question": q})] for q in ("a", "b", "c")] + ["never reached"])
    s = start_session(monkeypatch, chat=chat, tools=fake_tools)

    events = said(s.ask("Audit my battery notes."))

    assert len(fake_tools.calls) == 2  # the third never reached the server
    assert events[-1].text.startswith("I stopped here: the expert has already answered 2 times")
    assert "What's still unclear?" in events[-1].text

def test_a_failed_expert_call_does_not_use_up_the_turns_one_call(monkeypatch):
    results = iter(["FileNotFoundError: No such file: 'nuru/x.md'", EXPERT_ANSWER])
    fake_tools = FakeMCPTools(tools=[EXPERT_TOOL])
    fake_tools.call_tool = lambda name, arguments: (fake_tools.calls.append((name, arguments)), next(results))[1]
    chat = scripted_chat(
        [
            [tool_call("ask_an_expert", {"question": "q", "file_paths": ["nuru/x.md"]})],
            [tool_call("ask_an_expert", {"question": "q"})],
            "done",
        ]
    )
    s = start_session(monkeypatch, chat=chat, tools=fake_tools)

    said(s.ask("audit"))

    assert len(fake_tools.calls) == 2


def test_expert_attachments_named_by_title_are_resolved_to_vault_paths(monkeypatch):
    class Resolving(FakeMCPTools):
        def call_harness_tool(self, name, arguments):
            if name == "get_links" and arguments["name"] == "Area Monitor Device Redesign":
                return ToolResult(json.dumps({"note": "shared/Projects/area-monitor-device-redesign.md"}))
            return ToolResult("FileNotFoundError: no such note", is_error=True)

    fake_tools = Resolving(tools=[EXPERT_TOOL], tool_results={"ask_an_expert": EXPERT_ANSWER})
    chat = scripted_chat([[tool_call("ask_an_expert", {"question": "q", "file_paths": ["Area Monitor Device Redesign", "x.pdf"]})], "done"])
    s = start_session(monkeypatch, chat=chat, tools=fake_tools)

    said(s.ask("audit"))

    # the title became its path; a name the vault can't resolve goes on as given
    assert fake_tools.calls[0][1]["file_paths"] == ["shared/Projects/area-monitor-device-redesign.md", "x.pdf"]


def test_a_tool_that_returns_an_error_is_logged_as_an_error(monkeypatch, tmp_path):
    fake_tools = FakeMCPTools(tools=[{"type": "function", "function": {"name": "read_note", "description": "x", "parameters": {}}}])
    fake_tools.call_tool_result = lambda name, arguments: ToolResult("FileNotFoundError: no note 'x'", is_error=True)
    chat = scripted_chat([[tool_call("read_note", {"name": "x"})], "No such note."])
    s = start_session(monkeypatch, chat=chat, tools=fake_tools)

    said(s.ask("read x"))

    (log_file,) = tmp_path.glob("*.jsonl")
    (call,) = [e for e in map(json.loads, log_file.read_text().splitlines()) if e["event"] == "tool_call"]
    assert call["status"] == "error" and "no note 'x'" in call["error"]


def test_the_expert_limit_is_per_turn_not_per_conversation(monkeypatch):
    fake_tools = FakeMCPTools(tools=[EXPERT_TOOL], tool_results={"ask_an_expert": EXPERT_ANSWER})
    chat = scripted_chat(
        [
            [tool_call("ask_an_expert", {"question": "first"})],
            "first answer",
            [tool_call("ask_an_expert", {"question": "second"})],
            "second answer",
        ]
    )
    s = start_session(monkeypatch, chat=chat, tools=fake_tools)

    said(s.ask("first question"))
    said(s.ask("second question"))

    assert [args["question"] for _, args in fake_tools.calls] == ["first", "second"]


def test_start_connects_the_obsidian_deep_think_calc_and_laya_servers(monkeypatch):
    seen = {}

    def recording_mcp_tools(server_params, **kwargs):
        seen["params"] = server_params
        return FakeMCPTools()

    monkeypatch.setattr(session, "ensure_server_running", lambda: None)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model: None)
    monkeypatch.setattr(session, "MCPTools", recording_mcp_tools)

    Session.start()

    assert [p.args[-1] for p in seen["params"]] == ["obsidian-mcp", "deep-think-mcp", "calc-mcp", "laya-mcp"]


def test_index_change_reports_the_indexers_passes(monkeypatch):
    indexer = FakeIndexer()
    monkeypatch.setattr(session, "VaultIndexer", type("V", (), {"from_env": staticmethod(lambda: indexer)}))
    s = start_session(monkeypatch)
    assert s.index_change == (0, "")

    indexer.changes, indexer.last_change = 2, "indexed 1 note"

    assert s.index_change == (2, "indexed 1 note")
    s.close()


def test_an_index_pass_is_written_to_the_session_log(monkeypatch, tmp_path):
    indexer = FakeIndexer()
    monkeypatch.setattr(session, "VaultIndexer", type("V", (), {"from_env": staticmethod(lambda: indexer)}))
    s = start_session(monkeypatch)
    from obsidian_mcp.semantic import RefreshResult

    indexer.on_change(RefreshResult(embedded=["user/a.md"], moved=[("x.md", "y/x.md")], removed=["z.md"]))

    (log_file,) = tmp_path.glob("*.jsonl")
    events = [e for e in map(json.loads, log_file.read_text().splitlines()) if e["event"] == "index_pass"]
    assert events == [
        {**events[0], "embedded": ["user/a.md"], "moved": [{"from": "x.md", "to": "y/x.md"}], "removed": ["z.md"]}
    ]
    s.close()



def test_expert_results_say_which_answer_and_a_call_past_the_limit_is_a_clarifying_stop(monkeypatch):
    fake_tools = FakeMCPTools(tools=[EXPERT_TOOL], tool_results={"ask_an_expert": EXPERT_ANSWER})
    chat = scripted_chat([[tool_call("ask_an_expert", {"question": q})] for q in ("a", "b", "c")])
    s = start_session(monkeypatch, chat=chat, tools=fake_tools)

    events = list(s.ask("audit"))

    results = [e for e in events if isinstance(e, session.ToolResult)]
    assert [r.detail for r in results[:2]] == ["answer 1 of 2 this turn", "answer 2 of 2 this turn"]
    assert results[2].status == "blocked"
    assert isinstance(events[-1], session.Stopped) and events[-1].clarify
    assert s.expert_answers_used == 2


def test_status_reports_each_part_without_raising(monkeypatch):
    s = start_session(monkeypatch)

    status = s.status()

    assert set(status) == {"laya", "embedding", "expert", "think", "enforced"}
    assert status["laya"]["state"] in ("unknown", "down", "off")  # the fake has no Laya server
    assert status["embedding"]["state"] == "no vault"
    assert status["expert"]["key"] is False and status["expert"]["turn_limit"] == 2
