import json
import os
import subprocess
import time

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

    def close(self):
        self.closed = True


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


# --- create_model ---


def test_create_model_renders_the_base_model_placeholder_and_calls_ollama_create(monkeypatch, tmp_path):
    modelfile = tmp_path / "Modelfile"
    modelfile.write_text('FROM __BASE_MODEL__\n\nSYSTEM """hi"""\n')

    calls = []
    rendered_contents = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)
        # Read the rendered Modelfile now - create_model deletes it before returning.
        rendered_contents.append(open(cmd[4]).read())
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ollama.subprocess, "run", fake_run)

    ollama.create_model("nuru", "qwen3:8b", modelfile)

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[:3] == ["ollama", "create", "nuru"]
    assert cmd[3] == "-f"
    assert "FROM qwen3:8b" in rendered_contents[0]


def test_create_model_cleans_up_the_rendered_modelfile(monkeypatch, tmp_path):
    modelfile = tmp_path / "Modelfile"
    modelfile.write_text("FROM __BASE_MODEL__\n")

    rendered_paths = []

    def fake_run(cmd, check, capture_output, text):
        rendered_paths.append(cmd[4])
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ollama.subprocess, "run", fake_run)

    ollama.create_model("nuru", "qwen3:8b", modelfile)

    assert not os.path.exists(rendered_paths[0])


def test_create_model_cleans_up_even_if_ollama_create_fails(monkeypatch, tmp_path):
    modelfile = tmp_path / "Modelfile"
    modelfile.write_text("FROM __BASE_MODEL__\n")

    rendered_paths = []

    def failing_run(cmd, check, capture_output, text):
        rendered_paths.append(cmd[4])
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(ollama.subprocess, "run", failing_run)

    with pytest.raises(subprocess.CalledProcessError):
        ollama.create_model("nuru", "qwen3:8b", modelfile)

    assert not os.path.exists(rendered_paths[0])


# --- unload_all_models / stop_server ---


def test_unload_all_models_unloads_every_loaded_model(monkeypatch):
    monkeypatch.setattr(ollama, "loaded_models", lambda: [{"name": "qwen3:8b"}, {"name": "gemma4"}])

    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json))
        return FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)

    ollama.unload_all_models()

    assert calls == [
        (f"{ollama.BASE}/api/generate", {"model": "qwen3:8b", "keep_alive": 0}),
        (f"{ollama.BASE}/api/generate", {"model": "gemma4", "keep_alive": 0}),
    ]


def test_stop_server_does_nothing_if_we_did_not_start_it(monkeypatch):
    monkeypatch.setattr(ollama, "_server_process", None)

    ollama.stop_server()  # must not raise

    assert ollama._server_process is None


def test_stop_server_terminates_the_process_we_started(monkeypatch):
    class FakeProcess:
        def __init__(self):
            self.terminated = False

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            pass

    fake_process = FakeProcess()
    monkeypatch.setattr(ollama, "_server_process", fake_process)

    ollama.stop_server()

    assert fake_process.terminated is True
    assert ollama._server_process is None


def test_stop_server_kills_if_terminate_does_not_stop_it_in_time(monkeypatch):
    class FakeProcess:
        def __init__(self):
            self.killed = False

        def terminate(self):
            pass

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="ollama serve", timeout=timeout)

        def kill(self):
            self.killed = True

    fake_process = FakeProcess()
    monkeypatch.setattr(ollama, "_server_process", fake_process)

    ollama.stop_server()

    assert fake_process.killed is True


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


# --- is_model_loaded ---


def test_is_model_loaded_true_when_present(monkeypatch):
    monkeypatch.setattr(ollama, "loaded_models", lambda: [{"name": "qwen3:8b"}])
    assert ollama.is_model_loaded("qwen3:8b") is True


def test_is_model_loaded_false_when_absent(monkeypatch):
    monkeypatch.setattr(ollama, "loaded_models", lambda: [{"name": "other-model"}])
    assert ollama.is_model_loaded("qwen3:8b") is False


def test_is_model_loaded_true_when_queried_without_a_tag_but_loaded_as_latest(monkeypatch):
    # Regression: Ollama reports a loaded model with an explicit tag (e.g.
    # "nuru:latest") even when the caller asked for it without a tag ("nuru") -
    # a bare string comparison would always miss this and force a needless
    # reload before every single request.
    monkeypatch.setattr(ollama, "loaded_models", lambda: [{"name": "nuru:latest"}])
    assert ollama.is_model_loaded("nuru") is True


def test_is_model_loaded_true_when_queried_with_a_tag_matching_an_untagged_load(monkeypatch):
    monkeypatch.setattr(ollama, "loaded_models", lambda: [{"name": "nuru"}])
    assert ollama.is_model_loaded("nuru:latest") is True


# --- ensure_model_loaded ---


def test_ensure_model_loaded_skips_generate_call_if_already_loaded(monkeypatch):
    monkeypatch.setattr(ollama, "loaded_models", lambda: [{"name": "qwen3:8b"}])
    monkeypatch.setattr(
        ollama,
        "model_info",
        lambda model: {"details": {"parameter_size": "8B"}, "capabilities": ["thinking"]},
    )

    called = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: called.append(a) or FakeResponse())

    info = ollama.ensure_model_loaded("qwen3:8b")

    assert called == []
    assert info["parameter_size"] == "8B"
    assert info["supports_thinking"] is True


def test_ensure_model_loaded_extracts_context_window_and_embedding_length(monkeypatch):
    monkeypatch.setattr(ollama, "loaded_models", lambda: [{"name": "qwen3:8b"}])
    monkeypatch.setattr(
        ollama,
        "model_info",
        lambda model: {
            "details": {"family": "qwen3", "parameter_size": "8B"},
            "capabilities": [],
            "model_info": {"qwen3.context_length": 40960, "qwen3.embedding_length": 4096},
        },
    )

    info = ollama.ensure_model_loaded("qwen3:8b")

    assert info["context_window"] == 40960
    assert info["embedding_length"] == 4096


def test_ensure_model_loaded_calls_generate_if_not_loaded(monkeypatch):
    monkeypatch.setattr(ollama, "loaded_models", lambda: [])
    monkeypatch.setattr(
        ollama,
        "model_info",
        lambda model: {"details": {"parameter_size": "8B"}, "capabilities": []},
    )
    monkeypatch.setattr(ollama.time, "sleep", lambda _: None)

    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json))
        return FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)

    info = ollama.ensure_model_loaded("qwen3:8b")

    assert calls == [(f"{ollama.BASE}/api/generate", {"model": "qwen3:8b"})]
    assert info["supports_thinking"] is False


def test_ensure_model_loaded_shows_spinner_while_loading(monkeypatch):
    monkeypatch.setattr(ollama, "loaded_models", lambda: [])
    monkeypatch.setattr(
        ollama,
        "model_info",
        lambda model: {"details": {"parameter_size": "8B"}, "capabilities": []},
    )
    monkeypatch.setattr(ollama, "_SPIN_INTERVAL_SECONDS", 0.01)

    def fake_post(url, json, timeout):
        time.sleep(0.1)
        return FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)

    printed = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(a))

    ollama.ensure_model_loaded("qwen3:8b")

    assert any("Loading qwen3:8b" in "".join(str(x) for x in call) for call in printed)


# --- chat ---


def _sse_lines(*objs):
    return [json.dumps(obj).encode() for obj in objs]


def test_chat_yields_content_chunks_then_usage_dict(monkeypatch):
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


def test_chat_yields_the_thinking_before_the_usage_dict(monkeypatch):
    lines = _sse_lines(
        {"message": {"thinking": "Let me "}, "done": False},
        {"message": {"thinking": "check."}, "done": False},
        {"message": {"content": "Hi"}, "done": False},
        {"message": {"content": ""}, "done": True, "prompt_eval_count": 5, "eval_count": 2, "done_reason": "stop"},
    )
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(lines=lines))

    results = list(ollama.chat("qwen3:8b", [{"role": "user", "content": "hi"}]))

    assert results == [
        {"thinking_chunk": "Let me "},
        {"thinking_chunk": "check."},
        "Hi",
        {"thinking": "Let me check."},
        {"prompt_tokens": 5, "completion_tokens": 2, "stop_reason": "stop"},
    ]


def test_chat_closes_the_request_when_the_caller_stops_reading(monkeypatch):
    lines = _sse_lines(*[{"message": {"thinking": "again "}, "done": False}] * 50)
    response = FakeResponse(lines=lines)
    monkeypatch.setattr(requests, "post", lambda *a, **k: response)

    stream = ollama.chat("qwen3:8b", [{"role": "user", "content": "hi"}])
    next(stream)
    stream.close()

    assert response.closed is True


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


def test_chat_sends_tools_when_given(monkeypatch):
    seen = {}

    def fake_post(url, json, stream, timeout):
        seen["json"] = json
        return FakeResponse(lines=[])

    monkeypatch.setattr(requests, "post", fake_post)

    tools = [{"type": "function", "function": {"name": "get_temperature"}}]
    list(ollama.chat("qwen3:8b", [{"role": "user", "content": "hi"}], tools=tools))

    assert seen["json"]["tools"] == tools


def test_chat_omits_tools_key_when_not_given(monkeypatch):
    seen = {}

    def fake_post(url, json, stream, timeout):
        seen["json"] = json
        return FakeResponse(lines=[])

    monkeypatch.setattr(requests, "post", fake_post)

    list(ollama.chat("qwen3:8b", [{"role": "user", "content": "hi"}]))

    assert "tools" not in seen["json"]


def test_chat_sends_a_format_schema_only_when_given(monkeypatch):
    seen = []

    def fake_post(url, json, stream, timeout):
        seen.append(json)
        return FakeResponse(lines=[])

    monkeypatch.setattr(requests, "post", fake_post)

    schema = {"type": "object", "properties": {"text": {"type": "string"}}}
    list(ollama.chat("qwen3:8b", [{"role": "user", "content": "hi"}], fmt=schema))
    list(ollama.chat("qwen3:8b", [{"role": "user", "content": "hi"}]))

    assert seen[0]["format"] == schema
    assert "format" not in seen[1]


def test_chat_yields_tool_calls(monkeypatch):
    tool_call = {"function": {"name": "get_temperature", "arguments": {"city": "Berlin"}}}
    lines = _sse_lines(
        {"message": {"content": "", "tool_calls": [tool_call]}, "done": False},
        {"message": {"content": ""}, "done": True, "prompt_eval_count": 5, "eval_count": 2, "done_reason": "stop"},
    )
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(lines=lines))

    results = list(ollama.chat("qwen3:8b", [{"role": "user", "content": "hi"}]))

    assert results == [
        {"tool_calls": [tool_call]},
        {"prompt_tokens": 5, "completion_tokens": 2, "stop_reason": "stop"},
    ]


def test_chat_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(ok=False, status_code=404))

    with pytest.raises(requests.HTTPError):
        list(ollama.chat("nonexistent-model", []))
