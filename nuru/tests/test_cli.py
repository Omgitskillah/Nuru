import builtins

import pytest

from core import cli


@pytest.fixture(autouse=True)
def _isolate_log_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "LOG_DIR", tmp_path)


def fake_input(lines):
    """Feeds `lines` one at a time, then raises EOFError like input() does at EOF."""
    remaining = iter(lines)

    def _input(*_):
        try:
            return next(remaining)
        except StopIteration:
            raise EOFError

    return _input


def fake_chat(chunks):
    def _chat(model, messages, think=True, tools=None):
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


def test_main_echoes_response_prefixed_with_nuru(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", fake_input(["hello"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    monkeypatch.setattr(cli, "chat", fake_chat(["Hi", " there", {"prompt_tokens": 1, "completion_tokens": 2}]))
    patch_mcp(monkeypatch, FakeMCPTools())

    cli.main()

    out = capsys.readouterr().out
    assert "[nuru] Hi there" in out


def test_main_sends_each_line_as_its_own_message(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input(["first", "second"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    patch_mcp(monkeypatch, FakeMCPTools())

    sent = []

    def recording_chat(model, messages, think=True, tools=None):
        sent.append(messages)
        return iter([])

    monkeypatch.setattr(cli, "chat", recording_chat)

    cli.main()

    assert sent == [
        [{"role": "user", "content": "first"}],
        [{"role": "user", "content": "second"}],
    ]


def test_main_exits_cleanly_on_eof(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input([]))
    patch_mcp(monkeypatch, FakeMCPTools())

    cli.main()  # must not raise


def test_main_checks_server_and_model_before_each_send(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input(["hi"]))

    calls = []
    monkeypatch.setattr(cli, "ensure_server_running", lambda: calls.append("server"))
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: calls.append(("model", model)))
    monkeypatch.setattr(cli, "chat", fake_chat([]))
    patch_mcp(monkeypatch, FakeMCPTools())

    cli.main()

    assert calls == ["server", ("model", cli.MODEL)]


def test_main_passes_think_setting_to_chat(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input(["hi"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    monkeypatch.setattr(cli, "THINK", False)
    patch_mcp(monkeypatch, FakeMCPTools())

    seen = {}

    def recording_chat(model, messages, think=True, tools=None):
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

    def recording_chat(model, messages, think=True, tools=None):
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
    monkeypatch.setattr(cli, "is_model_loaded", lambda model: False)

    def unexpected_call(*a, **k):
        raise AssertionError("ensure_model_loaded should not be called for /show when not loaded")

    monkeypatch.setattr(cli, "ensure_model_loaded", unexpected_call)
    patch_mcp(monkeypatch, FakeMCPTools())

    cli.main()

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

    assert calls == ["server", ("model", cli.MODEL)]


def test_main_help_command_lists_commands_without_chatting(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", fake_input(["/help"]))
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

    def recording_chat(model, messages, think=True, tools=None):
        yield from next(responses)

    monkeypatch.setattr(cli, "chat", recording_chat)

    cli.main()

    assert fake_tools_client.calls == [("get_temperature", {"city": "Berlin"})]
    assert "[nuru] It's 17C" in capsys.readouterr().out
