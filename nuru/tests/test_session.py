import json
import threading

import pytest

from core import session
from core.conversation_store import ConversationStore
from core.session import Failure, Session, Text, ToolCall


@pytest.fixture(autouse=True)
def _isolate_log_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "LOG_DIR", tmp_path)
    monkeypatch.setattr(session, "DB_PATH", tmp_path / "conversation.db")
    # Building the custom Ollama model is a real `ollama create` subprocess
    # call - never let it run for real in a test.
    monkeypatch.setattr(session, "create_model", lambda model, base_model, modelfile_path: None)


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

    assert calls == [(session.MODEL, session.BASE_MODEL, session.MODELFILE_PATH)]


def test_start_connects_mcp_tools_and_passes_them_to_chat(monkeypatch):
    tools = [{"type": "function", "function": {"name": "get_temperature"}}]
    fake_tools = FakeMCPTools(tools=tools)
    seen = {}

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        seen["tools"] = tools
        return iter([])

    s = start_session(monkeypatch, chat=recording_chat, tools=fake_tools)
    list(s.ask("hi"))

    assert fake_tools.connected is True
    assert seen["tools"] == tools


def test_start_hides_delete_file_or_directory_from_the_local_model(monkeypatch):
    seen = {}

    def recording_mcp_tools(server_params, **kwargs):
        seen.update(kwargs)
        return FakeMCPTools()

    monkeypatch.setattr(session, "ensure_server_running", lambda: None)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model: None)
    monkeypatch.setattr(session, "MCPTools", recording_mcp_tools)

    Session.start()

    assert "delete_file_or_directory" in seen["hidden_tools"]
    assert "archive_note" not in seen["hidden_tools"]


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

    events = list(s.ask("hello"))

    assert events == [Text("Hi"), Text(" there")]
    saved = s.store.get_messages(s.conversation_id)
    assert [(m["role"], m["content"], m["tokens_used"]) for m in saved] == [("user", "hello", 1), ("nuru", "Hi there", 2)]


def test_ask_checks_the_server_and_the_model_before_each_send(monkeypatch):
    calls = []
    monkeypatch.setattr(session, "ensure_server_running", lambda: calls.append("server"))
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model: calls.append(("model", model)))
    monkeypatch.setattr(session, "chat", fake_chat([]))
    monkeypatch.setattr(session, "MCPTools", lambda server_params, **kwargs: FakeMCPTools())

    s = Session.start()
    list(s.ask("hi"))

    assert calls == ["server", ("model", session.MODEL), "server", ("model", session.MODEL)]


def test_ask_includes_prior_turns_from_the_same_conversation_as_history(monkeypatch):
    sent = []

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        sent.append(messages)
        return iter([])

    s = start_session(monkeypatch, chat=recording_chat)
    list(s.ask("first"))
    list(s.ask("second"))

    assert sent[0] == [{"role": "user", "content": "first"}]
    assert sent[1] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "second"},
    ]


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

    events = list(s.ask("what's the weather in Berlin?"))

    assert events == [ToolCall("get_temperature", {"city": "Berlin"}), Text("It's 17C")]
    assert fake_tools.calls == [("get_temperature", {"city": "Berlin"})]
    assert sent[1][-1] == {"role": "tool", "content": "17C"}


def test_ask_yields_the_tool_call_before_running_it(monkeypatch):
    fake_tools = FakeMCPTools()
    responses = iter([[{"tool_calls": [tool_call("slow_tool", {})]}], []])
    s = start_session(monkeypatch, chat=lambda *a, **k: iter(next(responses)), tools=fake_tools)

    turn = s.ask("go")
    event = next(turn)

    assert event == ToolCall("slow_tool", {})
    assert fake_tools.calls == []  # a UI gets to show the call while the tool is still running
    list(turn)
    assert fake_tools.calls == [("slow_tool", {})]


def test_ask_accepts_tool_arguments_sent_as_a_json_string(monkeypatch):
    fake_tools = FakeMCPTools()
    responses = iter([[{"tool_calls": [tool_call("get_temperature", '{"city": "Berlin"}')]}], []])
    s = start_session(monkeypatch, chat=lambda *a, **k: iter(next(responses)), tools=fake_tools)

    events = list(s.ask("weather?"))

    assert events == [ToolCall("get_temperature", {"city": "Berlin"})]
    assert fake_tools.calls == [("get_temperature", {"city": "Berlin"})]


def test_ask_fails_and_forgets_the_message_when_the_request_fails(monkeypatch):
    def failing_chat(model, messages, num_ctx=8192, think=True, tools=None):
        raise session.requests.exceptions.ReadTimeout("timed out")
        yield  # pragma: no cover - makes this a generator

    s = start_session(monkeypatch, chat=failing_chat)

    first = list(s.ask("hello"))
    second = list(s.ask("still here?"))  # the session keeps working after a failure

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

    events = list(s.ask("x" * 100_000))

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

    assert list(s.ask("hi")) == [Text("ok")]


def test_ask_stops_when_cancelled_and_keeps_what_was_said_so_far(monkeypatch, tmp_path):
    cancel = threading.Event()

    def chat_that_gets_stopped(model, messages, num_ctx=8192, think=True, tools=None):
        yield "Hel"
        cancel.set()  # the user hits stop while the model is still talking
        yield "lo"
        yield {"prompt_tokens": 1, "completion_tokens": 2}

    s = start_session(monkeypatch, chat=chat_that_gets_stopped)

    events = list(s.ask("hi", cancel))

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

    events = list(s.ask("go", cancel))

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
