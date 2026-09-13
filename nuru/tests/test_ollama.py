import json

import pytest
import requests

from core.providers import ollama


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, json_data=None, lines=None, ok=True, status_code=200):
        self._json_data = json_data
        self._lines = lines or []
        self.ok = ok
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code} error")

    def iter_lines(self):
        return iter(self._lines)


# --- is_server_running / ensure_server_running ---


def test_is_server_running_true_when_reachable(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(ok=True))
    assert ollama.is_server_running() is True


def test_is_server_running_false_when_connection_refused(monkeypatch):
    def raise_connection_error(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", raise_connection_error)
    assert ollama.is_server_running() is False


def test_ensure_server_running_does_nothing_if_already_up(monkeypatch):
    monkeypatch.setattr(ollama, "is_server_running", lambda: True)

    called = []
    monkeypatch.setattr(ollama.subprocess, "Popen", lambda *a, **k: called.append(a))

    ollama.ensure_server_running()

    assert called == []


def test_ensure_server_running_starts_and_waits_for_it(monkeypatch):
    calls = iter([False, False, True])
    monkeypatch.setattr(ollama, "is_server_running", lambda: next(calls))
    monkeypatch.setattr(ollama.time, "sleep", lambda _: None)

    popen_calls = []
    monkeypatch.setattr(ollama.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))

    ollama.ensure_server_running(timeout=5)

    assert popen_calls and popen_calls[0][0] == ["ollama", "serve"]


def test_ensure_server_running_raises_if_it_never_comes_up(monkeypatch):
    monkeypatch.setattr(ollama, "is_server_running", lambda: False)
    monkeypatch.setattr(ollama.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(ollama.time, "sleep", lambda _: None)

    # First call sets the deadline; every call after already exceeds it.
    times = iter([0.0])
    monkeypatch.setattr(ollama.time, "monotonic", lambda: next(times, 999.0))

    with pytest.raises(RuntimeError, match="did not come up"):
        ollama.ensure_server_running(timeout=5)


# --- loaded_models / model_info ---


def test_loaded_models_returns_models_list(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: FakeResponse(json_data={"models": [{"name": "qwen3:8b"}]})
    )
    assert ollama.loaded_models() == [{"name": "qwen3:8b"}]


def test_loaded_models_empty_when_key_missing(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(json_data={}))
    assert ollama.loaded_models() == []


def test_model_info_posts_to_show_endpoint(monkeypatch):
    seen = {}

    def fake_post(url, json, timeout):
        seen["url"] = url
        seen["json"] = json
        return FakeResponse(json_data={"details": {"family": "qwen3"}})

    monkeypatch.setattr(requests, "post", fake_post)

    info = ollama.model_info("qwen3:8b")

    assert seen["url"] == f"{ollama.BASE}/api/show"
    assert seen["json"] == {"model": "qwen3:8b"}
    assert info == {"details": {"family": "qwen3"}}


# --- ensure_model_loaded ---


def test_ensure_model_loaded_skips_generate_call_if_already_loaded(monkeypatch, capsys):
    monkeypatch.setattr(ollama, "loaded_models", lambda: [{"name": "qwen3:8b"}])
    monkeypatch.setattr(
        ollama,
        "model_info",
        lambda model: {"details": {"parameter_size": "8B"}, "capabilities": ["thinking"]},
    )

    called = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: called.append(a) or FakeResponse())

    ollama.ensure_model_loaded("qwen3:8b")

    assert called == []
    out = capsys.readouterr().out
    assert "qwen3:8b" in out
    assert "8B" in out
    assert "supports_thinking=True" in out


def test_ensure_model_loaded_calls_generate_if_not_loaded(monkeypatch, capsys):
    monkeypatch.setattr(ollama, "loaded_models", lambda: [])
    monkeypatch.setattr(
        ollama,
        "model_info",
        lambda model: {"details": {"parameter_size": "8B"}, "capabilities": []},
    )

    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json))
        return FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)

    ollama.ensure_model_loaded("qwen3:8b")

    assert calls == [(f"{ollama.BASE}/api/generate", {"model": "qwen3:8b"})]
    assert "supports_thinking=False" in capsys.readouterr().out


# --- chat ---


def _sse_lines(*objs):
    return [json.dumps(obj).encode() for obj in objs]


def test_chat_yields_content_chunks_then_usage_dict(monkeypatch, capsys):
    lines = _sse_lines(
        {"message": {"content": "Hel"}, "done": False},
        {"message": {"content": "lo"}, "done": False},
        {"message": {"content": ""}, "done": True, "prompt_eval_count": 5, "eval_count": 2, "done_reason": "stop"},
    )
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(lines=lines))

    results = list(ollama.chat("qwen3:8b", [{"role": "user", "content": "hi"}]))

    assert results == [
        "Hel",
        "lo",
        {"prompt_tokens": 5, "completion_tokens": 2, "stop_reason": "stop"},
    ]
    assert "[user] hi" in capsys.readouterr().out


def test_chat_skips_blank_lines(monkeypatch):
    lines = [b"", json.dumps({"message": {"content": "hi"}, "done": True}).encode()]
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(lines=lines))

    results = list(ollama.chat("qwen3:8b", []))

    assert results == ["hi", {"prompt_tokens": 0, "completion_tokens": 0, "stop_reason": None}]


def test_chat_sends_think_and_num_ctx_options(monkeypatch):
    seen = {}

    def fake_post(url, json, stream, timeout):
        seen["json"] = json
        return FakeResponse(lines=[])

    monkeypatch.setattr(requests, "post", fake_post)

    list(ollama.chat("qwen3:8b", [{"role": "user", "content": "hi"}], num_ctx=4096, think=False))

    assert seen["json"]["think"] is False
    assert seen["json"]["options"] == {"num_ctx": 4096}
    assert seen["json"]["stream"] is True


def test_chat_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(ok=False, status_code=404))

    with pytest.raises(requests.HTTPError):
        list(ollama.chat("nonexistent-model", []))
