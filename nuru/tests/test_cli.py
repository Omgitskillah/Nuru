import builtins

import pytest

from core import cli
from core.conversation_store import ConversationStore


@pytest.fixture(autouse=True)
def _isolate_log_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cli, "DB_PATH", tmp_path / "conversation.db")
    # Building the custom Ollama model is a real `ollama create` subprocess
    # call - never let it run for real in a test.
    monkeypatch.setattr(cli, "create_model", lambda model, base_model, modelfile_path: None)


def fake_input(lines):
    """Feeds `lines` one at a time, then raises EOFError like input() does at EOF."""
    remaining = iter(lines)

    def _input(*_):
        try:
            return next(remaining)
        except StopIteration:
            raise EOFError

    return _input


def fake_input_recording_prompts(lines, prompts):
    """Like fake_input, but also records each prompt string input() was called with."""
    remaining = iter(lines)

    def _input(prompt=""):
        prompts.append(prompt)
        try:
            return next(remaining)
        except StopIteration:
            raise EOFError

    return _input


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


def patch_mcp(monkeypatch, fake_tools_client):
    monkeypatch.setattr(cli, "MCPTools", lambda server_params: fake_tools_client)


def test_context_usage_percent_is_zero_for_an_empty_conversation(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()

    assert cli._context_usage_percent(store, conversation_id, context_window=1000) == 0


def test_context_usage_percent_reflects_full_context_window_not_the_reserved_budget(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    # 400 of a 1000-token window = 40%, even though the 80% response-reserved
    # budget (800) is what actually bounds which history gets included.
    store.add_message(conversation_id, "user", "hi", tokens_used=400)

    assert cli._context_usage_percent(store, conversation_id, context_window=1000) == 40


def test_context_usage_percent_is_capped_at_100(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    store.add_message(conversation_id, "user", "hi", tokens_used=5000)

    assert cli._context_usage_percent(store, conversation_id, context_window=1000) == 100


def test_context_usage_percent_uses_only_the_latest_turn_not_a_sum(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    # Each user row's tokens_used is already the FULL prompt size Ollama saw for
    # that call (history + tool schema + everything) - summing rows would
    # double-count prior turns' content. Only the latest one should matter.
    store.add_message(conversation_id, "user", "first", tokens_used=300)
    store.add_message(conversation_id, "nuru", "reply", tokens_used=50)
    store.add_message(conversation_id, "user", "second", tokens_used=350)

    assert cli._context_usage_percent(store, conversation_id, context_window=1000) == 35


def test_main_shows_context_usage_percent_in_the_input_prompt(monkeypatch):
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: {"context_window": 1000})
    monkeypatch.setattr(cli, "chat", fake_chat([]))
    patch_mcp(monkeypatch, FakeMCPTools())

    prompts = []
    monkeypatch.setattr(builtins, "input", fake_input_recording_prompts(["hi"], prompts))

    cli.main()

    assert prompts[0] == "[user: 0%] "


def test_main_echoes_response_prefixed_with_nuru(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", fake_input(["hello"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    monkeypatch.setattr(cli, "chat", fake_chat(["Hi", " there", {"prompt_tokens": 1, "completion_tokens": 2}]))
    patch_mcp(monkeypatch, FakeMCPTools())

    cli.main()

    out = capsys.readouterr().out
    assert "[nuru] Hi there" in out


def test_main_includes_prior_turns_from_the_same_conversation_as_history(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input(["first", "second"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    patch_mcp(monkeypatch, FakeMCPTools())

    sent = []

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        sent.append(messages)
        return iter([])

    monkeypatch.setattr(cli, "chat", recording_chat)

    cli.main()

    assert sent[0] == [{"role": "user", "content": "first"}]
    assert sent[1] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "second"},
    ]


def test_main_exits_cleanly_on_eof(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input([]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    patch_mcp(monkeypatch, FakeMCPTools())

    cli.main()  # must not raise


def test_main_builds_the_custom_model_from_the_modelfile_before_loading(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input([]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    patch_mcp(monkeypatch, FakeMCPTools())

    calls = []
    monkeypatch.setattr(
        cli, "create_model", lambda model, base_model, modelfile_path: calls.append((model, base_model, modelfile_path))
    )

    cli.main()

    assert calls == [(cli.MODEL, cli.BASE_MODEL, cli.MODELFILE_PATH)]


def test_main_checks_server_and_model_before_each_send(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input(["hi"]))

    calls = []
    monkeypatch.setattr(cli, "ensure_server_running", lambda: calls.append("server"))
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: calls.append(("model", model)))
    monkeypatch.setattr(cli, "chat", fake_chat([]))
    patch_mcp(monkeypatch, FakeMCPTools())

    cli.main()

    assert calls == ["server", ("model", cli.MODEL), "server", ("model", cli.MODEL)]


def test_main_passes_think_setting_to_chat(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input(["hi"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    monkeypatch.setattr(cli, "THINK", False)
    patch_mcp(monkeypatch, FakeMCPTools())

    seen = {}

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        seen["think"] = think
        return iter([])

    monkeypatch.setattr(cli, "chat", recording_chat)

    cli.main()

    assert seen["think"] is False


def test_main_does_not_print_nuru_prefix_for_empty_response(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", fake_input(["hi"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    monkeypatch.setattr(cli, "chat", fake_chat([{"prompt_tokens": 1, "completion_tokens": 0}]))
    patch_mcp(monkeypatch, FakeMCPTools())

    cli.main()

    assert "[nuru]" not in capsys.readouterr().out


def test_main_connects_and_closes_mcp_tools_and_passes_tools_to_chat(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input(["hi"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)

    tools = [{"type": "function", "function": {"name": "get_temperature"}}]
    fake_tools_client = FakeMCPTools(tools=tools)
    patch_mcp(monkeypatch, fake_tools_client)

    seen = {}

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        seen["tools"] = tools
        return iter([])

    monkeypatch.setattr(cli, "chat", recording_chat)

    cli.main()

    assert fake_tools_client.connected is True
    assert fake_tools_client.closed is True
    assert seen["tools"] == tools


def test_main_exit_command_unloads_models_and_stops_server_without_chatting(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input(["/exit"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    patch_mcp(monkeypatch, FakeMCPTools())

    calls = []
    monkeypatch.setattr(cli, "unload_all_models", lambda: calls.append("unload"))
    monkeypatch.setattr(cli, "stop_server", lambda: calls.append("stop"))

    def unexpected_chat(*a, **k):
        raise AssertionError("chat should not be called for /exit")

    monkeypatch.setattr(cli, "chat", unexpected_chat)

    cli.main()

    assert calls == ["unload", "stop"]


def test_main_show_command_prints_model_info_when_loaded(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", fake_input(["/show"]))
    monkeypatch.setattr(cli, "is_model_loaded", lambda model: True)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: {"family": "qwen3", "context_window": 4096})
    patch_mcp(monkeypatch, FakeMCPTools())

    def unexpected_chat(*a, **k):
        raise AssertionError("chat should not be called for /show")

    monkeypatch.setattr(cli, "chat", unexpected_chat)

    cli.main()

    out = capsys.readouterr().out
    assert "qwen3" in out
    assert "4096" in out


def test_main_show_command_prints_no_model_loaded_when_not_loaded(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", fake_input(["/show"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "is_model_loaded", lambda model: False)

    calls = []
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: calls.append(model) or {})
    patch_mcp(monkeypatch, FakeMCPTools())

    cli.main()

    # Only the startup load happens; /show itself must not trigger another one.
    assert calls == [cli.MODEL]
    assert "no model loaded" in capsys.readouterr().out


def test_main_load_command_loads_model_without_chatting(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input(["/load"]))
    patch_mcp(monkeypatch, FakeMCPTools())

    calls = []
    monkeypatch.setattr(cli, "ensure_server_running", lambda: calls.append("server") or True)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: calls.append(("model", model)) or {})

    def unexpected_chat(*a, **k):
        raise AssertionError("chat should not be called for /load")

    monkeypatch.setattr(cli, "chat", unexpected_chat)

    cli.main()

    assert calls == ["server", ("model", cli.MODEL), "server", ("model", cli.MODEL)]


def test_main_help_command_lists_commands_without_chatting(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", fake_input(["/help"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    patch_mcp(monkeypatch, FakeMCPTools())

    def unexpected_chat(*a, **k):
        raise AssertionError("chat should not be called for /help")

    monkeypatch.setattr(cli, "chat", unexpected_chat)

    cli.main()

    out = capsys.readouterr().out
    assert "/load" in out
    assert "/show" in out
    assert "/exit" in out


def test_main_executes_tool_call_and_sends_result_back_to_model(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", fake_input(["what's the weather in Berlin?"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)

    fake_tools_client = FakeMCPTools(tool_results={"get_temperature": "17C"})
    patch_mcp(monkeypatch, fake_tools_client)

    tool_call = {"function": {"name": "get_temperature", "arguments": {"city": "Berlin"}}}
    responses = iter(
        [
            [{"tool_calls": [tool_call]}, {"prompt_tokens": 1, "completion_tokens": 1}],
            ["It's 17C", {"prompt_tokens": 2, "completion_tokens": 2}],
        ]
    )

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        yield from next(responses)

    monkeypatch.setattr(cli, "chat", recording_chat)

    cli.main()

    assert fake_tools_client.calls == [("get_temperature", {"city": "Berlin"})]
    assert "[nuru] It's 17C" in capsys.readouterr().out


def test_main_survives_a_request_failure_instead_of_crashing(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", fake_input(["hello", "still here?"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    patch_mcp(monkeypatch, FakeMCPTools())

    def failing_chat(model, messages, num_ctx=8192, think=True, tools=None):
        raise cli.requests.exceptions.ReadTimeout("timed out")
        yield  # pragma: no cover - makes this a generator

    monkeypatch.setattr(cli, "chat", failing_chat)

    cli.main()  # must not raise

    out = capsys.readouterr().out
    # Both turns ran (the session kept going after the first failure) instead
    # of the process crashing after the first request timed out.
    assert out.count("[error]") == 2


def test_main_deletes_the_user_message_when_the_request_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(builtins, "input", fake_input(["hello"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    patch_mcp(monkeypatch, FakeMCPTools())

    def failing_chat(model, messages, num_ctx=8192, think=True, tools=None):
        raise cli.requests.exceptions.ReadTimeout("timed out")
        yield  # pragma: no cover - makes this a generator

    monkeypatch.setattr(cli, "chat", failing_chat)

    cli.main()

    store = ConversationStore(cli.DB_PATH)
    messages = store.get_messages(1)
    assert messages == []


def test_estimate_tokens_uses_chars_per_token_heuristic():
    assert cli._estimate_tokens("a" * 400) == 100


def test_token_budget_reserves_the_response_fraction():
    assert cli._token_budget(1000) == 800


def test_main_rejects_a_message_that_would_exceed_the_token_budget(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", fake_input(["x" * 100_000]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: {"context_window": 1000})
    patch_mcp(monkeypatch, FakeMCPTools())

    def unexpected_chat(*a, **k):
        raise AssertionError("chat should not be called for an over-budget message")

    monkeypatch.setattr(cli, "chat", unexpected_chat)

    cli.main()

    out = capsys.readouterr().out
    assert "[error] message too long" in out

    store = ConversationStore(cli.DB_PATH)
    assert store.get_messages(1) == []


def test_main_accepts_a_message_within_the_token_budget(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", fake_input(["hi"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: {"context_window": 1000})
    monkeypatch.setattr(cli, "chat", fake_chat(["ok", {"prompt_tokens": 1, "completion_tokens": 1}]))
    patch_mcp(monkeypatch, FakeMCPTools())

    cli.main()

    out = capsys.readouterr().out
    assert "[error] message too long" not in out
    assert "[nuru] ok" in out
