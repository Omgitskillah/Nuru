"""Claude as Nuru's main model: core.providers.claude (message conversion, the request it builds, what
it yields, the local request log, errors) and the session's Claude path. No network: the claude-access
provider is faked."""

import json

import anthropic
import httpx
import pytest
import requests

from core import session
from core.providers import claude
from test_session import FakeMCPTools, start_session, tool_call


class FakeProvider:
    """Stands in for claude_access's ClaudeProvider: records each request, plays back events."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def stream_message(self, **request):
        self.requests.append(request)
        yield from self.responses.pop(0)


def reply(*events, content=(), stop="end_turn", usage=None):
    message = {"content": list(content), "stop_reason": stop, "model": "claude-haiku-5-5",
               "usage": usage or {"input_tokens": 10, "output_tokens": 5}}
    return [*events, {"type": "message", "message": message}]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(claude, "LOG_DIR", tmp_path)

    def install(*responses):
        provider = FakeProvider(*responses)
        monkeypatch.setattr(claude, "_provider", provider)
        return provider

    return install


TOOL = {"type": "function", "function": {"name": "search", "description": "Search notes.",
                                         "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}


# --- to_anthropic ---


def test_tool_results_answer_the_tool_uses_before_them_in_order():
    messages = [
        {"role": "user", "content": "find it"},
        {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "search", "arguments": {"query": "x"}}},
                                             {"id": "b", "function": {"name": "read_note", "arguments": "{\"name\": \"n\"}"}}]},
        {"role": "tool", "content": "found x"},
        {"role": "tool", "content": "note text"},
        {"role": "user", "content": "[Nuru] answer now"},
    ]
    assert claude.to_anthropic(messages) == [
        {"role": "user", "content": [{"type": "text", "text": "find it"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "search", "input": {"query": "x"}},
                                          {"type": "tool_use", "id": "b", "name": "read_note", "input": {"name": "n"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "found x"},
                                     {"type": "tool_result", "tool_use_id": "b", "content": "note text"},
                                     {"type": "text", "text": "[Nuru] answer now"}]},
    ]


def test_an_unanswered_tool_use_gets_a_not_run_result_and_calls_without_ids_get_one():
    messages = [{"role": "user", "content": "q"},
                {"role": "assistant", "tool_calls": [tool_call("search", {"query": "x"}), tool_call("search", {"query": "y"})]},
                {"role": "tool", "content": "found x"}]
    converted = claude.to_anthropic(messages)
    ids = [b["id"] for b in converted[1]["content"]]
    assert len(set(ids)) == 2 and all(i.startswith("toolu_nuru_") for i in ids)
    assert converted[2]["content"] == [{"type": "tool_result", "tool_use_id": ids[0], "content": "found x"},
                                       {"type": "tool_result", "tool_use_id": ids[1], "content": "Not run."}]


def test_the_providers_own_content_is_sent_back_unchanged_thinking_blocks_included():
    content = [{"type": "thinking", "thinking": "look it up", "signature": "sig"},
               {"type": "tool_use", "id": "t1", "name": "search", "input": {"query": "x"}}]
    messages = [{"role": "user", "content": "q"},
                {"role": "assistant", "tool_calls": [{"id": "t1", "function": {"name": "search", "arguments": {}}}],
                 "provider_content": content, "thinking": "look it up"},
                {"role": "tool", "content": "r"}]
    converted = claude.to_anthropic(messages)
    assert converted[1] == {"role": "assistant", "content": content}
    assert converted[2]["content"][0]["tool_use_id"] == "t1"


# --- chat ---


def test_chat_streams_text_then_yields_usage_with_the_cache_counts(fake):
    provider = fake(reply({"type": "thinking", "text": "hm"}, {"type": "text", "text": "Hi"},
                          usage={"input_tokens": 12, "output_tokens": 4, "cache_read_input_tokens": 6000,
                                 "cache_creation_input_tokens": 300}))
    out = list(claude.chat("claude-haiku-5-5", [{"role": "user", "content": "hi"}], think="low", tools=[TOOL],
                           options={"temperature": 0.7}, system="You are Nuru."))
    assert out == [{"thinking_chunk": "hm"}, "Hi", {"thinking": "hm"},
                   {"prompt_tokens": 6312, "completion_tokens": 4, "stop_reason": "stop",
                    "cache_read_tokens": 6000, "cache_write_tokens": 300}]
    (request,) = provider.requests
    assert request["system"] == [{"type": "text", "text": "You are Nuru.", "cache_control": {"type": "ephemeral"}}]
    assert request["tools"] == [{"name": "search", "description": "Search notes.", "input_schema": TOOL["function"]["parameters"],
                                 "cache_control": {"type": "ephemeral"}}]
    assert request["thinking"] == {"type": "adaptive"} and request["output_config"] == {"effort": "low"}
    assert "temperature" not in json.dumps(request)  # sampling options aren't sent


def test_chat_yields_tool_calls_with_their_ids_then_the_whole_content(fake):
    content = [{"type": "thinking", "thinking": "search first", "signature": "s"},
               {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {"query": "keepalive"}}]
    fake(reply({"type": "thinking", "text": "search first"}, content=content, stop="tool_use"))
    out = list(claude.chat("claude-haiku-5-5", [{"role": "user", "content": "q"}], think=True, tools=[TOOL]))
    assert out[1] == {"tool_calls": [{"id": "toolu_1", "function": {"name": "search", "arguments": {"query": "keepalive"}}}]}
    assert out[2] == {"provider_content": content}
    assert out[-1]["stop_reason"] == "stop"


def test_thinking_off_disables_it_and_a_schema_becomes_structured_output(fake):
    provider = fake(reply({"type": "text", "text": "{}"}))
    schema = {"type": "object", "properties": {}}
    list(claude.chat("claude-haiku-5-5", [{"role": "user", "content": "q"}], think=False, fmt=schema))
    (request,) = provider.requests
    assert request["thinking"] == {"type": "disabled"}
    assert request["output_config"] == {"format": {"type": "json_schema", "schema": schema}}
    assert request["system"] is None and request["tools"] is None


def test_thinking_off_is_sent_the_way_each_model_takes_it(fake):
    provider = fake(reply({"type": "text", "text": "a"}), reply({"type": "text", "text": "b"}))
    list(claude.chat("claude-haiku-5-5", [{"role": "user", "content": "q"}], think=False))
    list(claude.chat("claude-sonnet-5-5", [{"role": "user", "content": "q"}], think=False))
    assert [r["thinking"] for r in provider.requests] == [{"type": "disabled"}, {"type": "between_tools"}]


def test_every_request_is_logged_locally_with_what_was_sent_and_the_usage(fake, tmp_path):
    fake(reply({"type": "text", "text": "ok"}))
    list(claude.chat("claude-haiku-5-5", [{"role": "user", "content": "the note says 42"}], think=False, tools=[TOOL]))
    (log,) = tmp_path.glob("claude-requests-*.jsonl")
    entry = json.loads(log.read_text())
    assert entry["model"] == "claude-haiku-5-5" and entry["tools"] == ["search"]
    assert entry["messages"] == [{"role": "user", "content": [{"type": "text", "text": "the note says 42"}]}]
    assert entry["usage"] == {"input_tokens": 10, "output_tokens": 5}


def test_an_api_error_is_a_request_exception_and_is_logged(fake, tmp_path, monkeypatch):
    error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))

    def failing(**request):
        raise error
        yield

    monkeypatch.setattr(claude, "_provider", type("P", (), {"stream_message": staticmethod(failing)})())
    with pytest.raises(requests.exceptions.RequestException, match="Claude API error"):
        list(claude.chat("claude-haiku-5-5", [{"role": "user", "content": "q"}]))
    (log,) = tmp_path.glob("claude-requests-*.jsonl")
    assert "APIConnectionError" in json.loads(log.read_text())["error"]


def test_only_the_two_supported_models_are_accepted():
    assert claude.model_id("anthropic:claude-haiku-5-5") == "claude-haiku-5-5"
    assert claude.model_id("anthropic:claude-sonnet-5-5") == "claude-sonnet-5-5"
    with pytest.raises(ValueError, match="Unsupported main model"):
        claude.model_id("anthropic:claude-opus-5-5")


# --- the session's Claude path ---


@pytest.fixture
def claude_session(monkeypatch):
    monkeypatch.setattr(session, "CLAUDE", True)
    monkeypatch.setattr(session, "MODEL", "claude-haiku-5-5")
    monkeypatch.setattr(session, "create_model", lambda *a: pytest.fail("no Modelfile is built for Claude"))
    monkeypatch.setattr(session, "unload_all_models", lambda: pytest.fail("Claude must leave Ollama's models alone"))


def test_with_claude_no_modelfile_is_built_and_the_system_prompt_goes_with_each_request(claude_session, monkeypatch):
    seen = []

    def chat(model, messages, num_ctx=8192, think=True, tools=None, system=None, **kwargs):
        seen.append((model, system, num_ctx))
        yield "Hello."
        yield {"prompt_tokens": 1, "completion_tokens": 1}

    s = start_session(monkeypatch, chat=chat)
    assert list(s.ask("hi"))
    (model, system, num_ctx), = seen
    assert model == "claude-haiku-5-5"
    assert "Nuru" in system and system == s._system_text()
    assert num_ctx == min(1_000_000, session.MAX_NUM_CTX)
    s.shutdown("test")  # leaves Ollama alone (claude_session fails the test otherwise)


def test_with_claude_the_tool_call_message_keeps_the_providers_content(claude_session, monkeypatch):
    content = [{"type": "thinking", "thinking": "t", "signature": "s"},
               {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {"query": "x"}}]
    calls = []

    def chat(model, messages, num_ctx=8192, think=True, tools=None, system=None, **kwargs):
        calls.append([dict(m) for m in messages])
        if len(calls) == 1:
            yield {"tool_calls": [{"id": "toolu_1", "function": {"name": "search", "arguments": {"query": "x"}}}]}
            yield {"provider_content": content}
        else:
            yield "Found it."
        yield {"prompt_tokens": 1, "completion_tokens": 1}

    s = start_session(monkeypatch, chat=chat, tools=FakeMCPTools(tool_results={"search": "{\"matches\": []}"}))
    list(s.ask("find x"))
    assistant = next(m for m in calls[1] if m["role"] == "assistant")
    assert assistant["provider_content"] == content
