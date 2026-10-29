from unittest.mock import MagicMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from claude_access.claude import ClaudeProvider
from claude_access.contract import Provider
from deep_think_mcp import server
from deep_think_mcp.providers import get_provider


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    get_provider.cache_clear()
    yield
    get_provider.cache_clear()


def test_default_provider_is_claude(monkeypatch):
    monkeypatch.delenv("DEEP_THINK_PROVIDER", raising=False)

    assert isinstance(get_provider(), ClaudeProvider)


def test_provider_selected_via_env_var(monkeypatch):
    monkeypatch.setenv("DEEP_THINK_PROVIDER", "claude")

    assert isinstance(get_provider(), ClaudeProvider)


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("DEEP_THINK_PROVIDER", "does-not-exist")

    with pytest.raises(ValueError, match="Unknown DEEP_THINK_PROVIDER"):
        get_provider()


def test_write_plan_asks_haiku_at_its_lowest_thinking_for_the_schema(monkeypatch):
    fake_provider = MagicMock(spec=Provider)
    fake_provider.send_message.return_value = {
        "content": [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": '{"steps":[]}'}],
        "stop_reason": "end_turn",
    }
    monkeypatch.setattr(server, "get_provider", lambda: fake_provider)
    schema = {"type": "object", "properties": {"steps": {"type": "array"}}}

    result = server.write_plan(request="Plan this.", instructions="You plan.", schema=schema)

    fake_provider.send_message.assert_called_once_with(
        model="claude-haiku-4-5",
        messages=[{"role": "user", "content": "Plan this."}],
        system="You plan.",
        max_tokens=4096,
        thinking={"type": "enabled", "budget_tokens": 1024},
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    assert result == {"plan": '{"steps":[]}', "stop_reason": "end_turn", "model": "claude-haiku-4-5"}


def test_write_plan_runs_sonnet_at_low_effort_with_the_refusal_fallback(monkeypatch):
    fake_provider = MagicMock(spec=Provider)
    fake_provider.send_message.return_value = {"content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn"}
    monkeypatch.setattr(server, "get_provider", lambda: fake_provider)

    server.write_plan(request="Plan this.", instructions="You plan.", model="claude-sonnet-5-5")

    fake_provider.send_message.assert_called_once_with(
        model="claude-sonnet-5-5",
        messages=[{"role": "user", "content": "Plan this."}],
        system="You plan.",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
        fallbacks="default",
    )


def test_write_plan_refuses_an_unknown_model():
    with pytest.raises(ToolError, match="model must be one of"):
        server.write_plan(request="Plan this.", instructions="You plan.", model="claude-opus-5")


def test_write_plan_says_when_there_is_no_plan(monkeypatch):
    fake_provider = MagicMock(spec=Provider)
    fake_provider.send_message.return_value = {"content": [], "stop_reason": "end_turn"}
    monkeypatch.setattr(server, "get_provider", lambda: fake_provider)

    result = server.write_plan(request="Plan this.", instructions="You plan.")

    assert result["plan"] == "" and "no plan" in result["hint"]
    assert fake_provider.send_message.call_args.kwargs["output_config"] is None


# ---------------------------------------------------------------- F13: the remaining edges
def test_an_empty_request_is_refused_without_asking_the_planner(monkeypatch):
    monkeypatch.setattr(server, "get_provider", lambda: pytest.fail("the planner was asked"))
    with pytest.raises(ToolError, match="request is empty"):
        server.write_plan(request="   ", instructions="You plan.")


def test_a_plan_cut_off_by_its_token_limit_says_so(monkeypatch):
    fake_provider = MagicMock(spec=Provider)
    fake_provider.send_message.return_value = {"content": [{"type": "text", "text": "1. Read"}], "stop_reason": "max_tokens"}
    monkeypatch.setattr(server, "get_provider", lambda: fake_provider)
    result = server.write_plan(request="Plan this.", instructions="You plan.")
    assert result["plan"] == "1. Read" and "ran out of room" in result["hint"]


def test_the_server_runs_its_mcp_loop(monkeypatch):
    import deep_think_mcp
    ran = []
    monkeypatch.setattr(server.mcp, "run", lambda: ran.append(True))
    deep_think_mcp.main()
    assert ran == [True]
