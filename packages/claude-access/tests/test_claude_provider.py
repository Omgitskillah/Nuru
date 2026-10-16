from unittest.mock import MagicMock

import pytest

from types import SimpleNamespace

from claude_access import keys
from claude_access.claude import ClaudeProvider


def _fake_response(data: dict) -> MagicMock:
    """A stand-in for an SDK response object whose .to_dict() returns data."""
    response = MagicMock()
    response.to_dict.return_value = data
    return response


@pytest.fixture
def client():
    """A fake anthropic.Anthropic client - no real network call ever happens."""
    return MagicMock()


@pytest.fixture
def provider(client):
    return ClaudeProvider(client=client)


# --- send_message ---


def test_send_message_calls_the_client_and_returns_its_response(provider, client):
    client.messages.create.return_value = _fake_response({"role": "assistant", "content": []})

    response = provider.send_message(model="claude-opus-5", messages=[{"role": "user", "content": "hi"}])

    client.messages.create.assert_called_once_with(
        model="claude-opus-5", max_tokens=1024, messages=[{"role": "user", "content": "hi"}]
    )
    assert response == {"role": "assistant", "content": []}


def test_send_message_forwards_system_prompt(provider, client):
    client.messages.create.return_value = _fake_response({})

    provider.send_message(model="claude-opus-5", messages=[{"role": "user", "content": "hi"}], system="be terse")

    client.messages.create.assert_called_once_with(
        model="claude-opus-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": "hi"}],
        system="be terse",
    )


def test_send_message_forwards_tools(provider, client):
    client.messages.create.return_value = _fake_response({})
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]

    provider.send_message(model="claude-opus-5", messages=[{"role": "user", "content": "hi"}], tools=tools)

    client.messages.create.assert_called_once_with(
        model="claude-opus-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": "hi"}],
        tools=tools,
    )


def test_send_message_raises_on_empty_messages_without_calling_client(provider, client):
    with pytest.raises(ValueError):
        provider.send_message(model="claude-opus-5", messages=[])

    client.messages.create.assert_not_called()


# --- count_tokens ---


def test_count_tokens_calls_the_client_and_returns_its_response(provider, client):
    client.messages.count_tokens.return_value = _fake_response({"input_tokens": 42})

    response = provider.count_tokens(model="claude-opus-5", messages=[{"role": "user", "content": "hi"}])

    client.messages.count_tokens.assert_called_once_with(
        model="claude-opus-5", messages=[{"role": "user", "content": "hi"}]
    )
    assert response == {"input_tokens": 42}


def test_count_tokens_raises_on_empty_messages_without_calling_client(provider, client):
    with pytest.raises(ValueError):
        provider.count_tokens(model="claude-opus-5", messages=[])

    client.messages.count_tokens.assert_not_called()


# --- list_models ---


def test_list_models_flattens_the_paginated_response(provider, client):
    page = MagicMock()
    page.data = [_fake_response({"id": "claude-opus-5"}), _fake_response({"id": "claude-sonnet-5"})]
    page.has_more = False
    page.first_id = "claude-opus-5"
    page.last_id = "claude-sonnet-5"
    client.models.list.return_value = page

    response = provider.list_models()

    assert response == {
        "data": [{"id": "claude-opus-5"}, {"id": "claude-sonnet-5"}],
        "has_more": False,
        "first_id": "claude-opus-5",
        "last_id": "claude-sonnet-5",
    }


def test_send_message_forwards_thinking_and_output_config(provider, client):
    client.messages.create.return_value = _fake_response({})
    thinking = {"type": "enabled", "budget_tokens": 1024}
    output_config = {"format": {"type": "json_schema", "schema": {"type": "object"}}}

    provider.send_message(
        model="claude-haiku-4-5", messages=[{"role": "user", "content": "hi"}], thinking=thinking,
        output_config=output_config,
    )

    client.messages.create.assert_called_once_with(
        model="claude-haiku-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": "hi"}],
        thinking=thinking,
        output_config=output_config,
    )


def test_send_message_with_fallbacks_uses_the_beta_endpoint(provider, client):
    client.beta.messages.create.return_value = _fake_response({})

    provider.send_message(model="claude-sonnet-5-5", messages=[{"role": "user", "content": "hi"}], fallbacks="default")

    client.beta.messages.create.assert_called_once_with(
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        model="claude-sonnet-5-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": "hi"}],
    )
    client.messages.create.assert_not_called()


# --- stream_message ---


class _FakeStream:
    """What client.messages.stream(...) returns: a context manager that iterates events."""

    def __init__(self, events, final):
        self._events, self._final = events, final
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return _fake_response(self._final)


def test_stream_message_yields_text_and_thinking_then_the_whole_message(provider, client):
    final = {"content": [{"type": "text", "text": "Hi"}], "stop_reason": "end_turn", "usage": {"output_tokens": 2}}
    stream = _FakeStream([SimpleNamespace(type="thinking", thinking="hmm"), SimpleNamespace(type="text", text="Hi"),
                          SimpleNamespace(type="content_block_stop")], final)
    client.messages.stream.return_value = stream

    events = list(provider.stream_message(model="claude-haiku-5-5", messages=[{"role": "user", "content": "hi"}],
                                          system=[{"type": "text", "text": "be terse"}]))

    assert events == [{"type": "thinking", "text": "hmm"}, {"type": "text", "text": "Hi"},
                      {"type": "message", "message": final}]
    client.messages.stream.assert_called_once_with(
        model="claude-haiku-5-5", max_tokens=1024, messages=[{"role": "user", "content": "hi"}],
        system=[{"type": "text", "text": "be terse"}])
    assert stream.closed


def test_stream_message_with_fallbacks_uses_the_beta_endpoint(provider, client):
    client.beta.messages.stream.return_value = _FakeStream([], {"content": []})

    list(provider.stream_message(model="claude-sonnet-5-5", messages=[{"role": "user", "content": "hi"}],
                                 fallbacks="default"))

    assert client.beta.messages.stream.call_args.kwargs["fallbacks"] == "default"
    client.messages.stream.assert_not_called()


def test_stream_message_raises_on_empty_messages_without_calling_client(provider, client):
    with pytest.raises(ValueError):
        list(provider.stream_message(model="claude-haiku-5-5", messages=[]))
    client.messages.stream.assert_not_called()


# --- keys ---


def test_api_key_prefers_the_environment_then_the_deep_think_env_file(monkeypatch, tmp_path):
    monkeypatch.setattr(keys, "REPO_ROOT", tmp_path)
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert keys.api_key() is None
    (tmp_path / ".env.deep-think").write_text("CLAUDE_API_KEY=from-file\n")
    assert keys.api_key() == "from-file"
    monkeypatch.setenv("CLAUDE_API_KEY", "from-claude-env")
    assert keys.api_key() == "from-claude-env"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-anthropic-env")
    assert keys.api_key() == "from-anthropic-env"
