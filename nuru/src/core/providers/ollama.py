"""Thin client for a local Ollama server (https://ollama.com), used to spin up
the server and a model on demand and stream chat responses from it.
"""

import json
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

import requests

from core.modelfile import render_modelfile

BASE = "http://localhost:11434"

# Local generation over a large context can legitimately take a long time on
# modest hardware - default generously rather than have a slow-but-working
# request get killed as if it had failed. Override via env if needed.
CHAT_READ_TIMEOUT_SECONDS = int(os.environ.get("NURU_CHAT_TIMEOUT_SECONDS", 1800))

# Handle for the `ollama serve` process we spawned, if any (set by
# ensure_server_running, used by stop_server to shut it back down).
_server_process: subprocess.Popen | None = None


def is_server_running() -> bool:
    try:
        return requests.get(f"{BASE}/api/tags", timeout=2).ok
    except requests.ConnectionError:
        return False


def ensure_server_running(timeout: float = 15) -> bool:
    """Start `ollama serve` in the background if it isn't already up.

    Returns whether this call spawned it (False if it was already running).
    """
    global _server_process

    if is_server_running():
        return False

    print("Ollama not running, starting it...")
    _server_process = subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_server_running():
            print("Ollama started.")
            return True
        time.sleep(0.5)
    raise RuntimeError(f"ollama serve did not come up within {timeout}s")


def create_model(model: str, base_model: str, modelfile_path: Path) -> str:
    """(Re)builds `model` in Ollama from the Modelfile template at `modelfile_path`,
    with its shared SYSTEM prompt and __BASE_MODEL__ placeholder resolved.

    `ollama create` overwrites an existing model of the same name, so this is
    safe (and intended) to call on every startup - it keeps `model`'s baked-in
    system prompt in sync with the template without ever needing a manual
    `ollama create` step. Returns the exact rendering baked into the model.
    """
    rendered = render_modelfile(modelfile_path, base_model)
    with tempfile.NamedTemporaryFile("w", suffix=".Modelfile", delete=False) as f:
        f.write(rendered)
        rendered_path = f.name
    try:
        subprocess.run(["ollama", "create", model, "-f", rendered_path], check=True, capture_output=True, text=True)
    finally:
        os.remove(rendered_path)
    return rendered


def unload_all_models() -> None:
    """Unloads every model currently resident in memory."""
    for m in loaded_models():
        r = requests.post(f"{BASE}/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=30)
        r.raise_for_status()


def stop_server() -> None:
    """Stops the `ollama serve` process we started, if we started it."""
    global _server_process

    if _server_process is None:
        return

    _server_process.terminate()
    try:
        _server_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _server_process.kill()
    _server_process = None


def loaded_models() -> list[dict]:
    """Models currently resident in memory (the 'active' ones)."""
    r = requests.get(f"{BASE}/api/ps", timeout=5)
    r.raise_for_status()
    return r.json().get("models", [])


def model_info(model: str) -> dict:
    """Static info about a model: architecture, size, quantization, default params."""
    r = requests.post(f"{BASE}/api/show", json={"model": model}, timeout=10)
    r.raise_for_status()
    return r.json()


def _normalize_model_name(name: str) -> str:
    """Ollama reports a loaded model's name with an explicit tag (e.g. `nuru:latest`)
    even when it was referenced without one - normalize both sides before comparing,
    or an untagged reference never matches and looks perpetually unloaded."""
    return name if ":" in name else f"{name}:latest"


def is_model_loaded(model: str) -> bool:
    """Whether `model` is currently resident in memory."""
    target = _normalize_model_name(model)
    return any(_normalize_model_name(m["name"]) == target for m in loaded_models())


_SPIN_INTERVAL_SECONDS = 1


DEFAULT_KEEP_ALIVE = "30m"


def _keep_alive() -> dict:
    """NURU_KEEP_ALIVE: how long Ollama keeps the model loaded after each request - a duration
    ("30m", "2h"), seconds ("600"), or "-1" for until Ollama stops. Read per request, since Ollama
    resets the model's timer to each request's own keep_alive. Unset: 30 minutes, long enough for a
    pause in work, short enough to free the memory after a real break."""
    value = os.environ.get("NURU_KEEP_ALIVE", "").strip() or DEFAULT_KEEP_ALIVE
    return {"keep_alive": int(value) if value.lstrip("-").isdigit() else value}


def _load_model_with_spinner(model: str, num_ctx: int | None = None) -> None:
    """Issues the blocking load request while printing increasing dots once a second."""
    done = threading.Event()

    def spin() -> None:
        dots = 0
        while not done.wait(_SPIN_INTERVAL_SECONDS):
            dots += 1
            print(f"\rLoading {model}" + "." * dots, end="", flush=True)

    spinner = threading.Thread(target=spin, daemon=True)
    spinner.start()
    try:
        body = {"model": model, **({"options": {"num_ctx": num_ctx}} if num_ctx else {}), **_keep_alive()}
        r = requests.post(f"{BASE}/api/generate", json=body, timeout=300)
        r.raise_for_status()
    finally:
        done.set()
        spinner.join()
        print("\r" + " " * (len(model) + 20) + "\r", end="", flush=True)


def ensure_model_loaded(model: str, window: Callable[[dict], int] | None = None) -> dict:
    """Load `model` into memory first if it isn't resident yet.

    Returns its static info (parameter_size, quantization_level, family, supports_thinking).
    `window(info)`, if given, is the num_ctx the chats will ask for: the model is loaded at that
    size, since Ollama starts a model again when a request's num_ctx differs from the loaded one's.
    """
    info = _static_info(model)
    if not is_model_loaded(model):
        _load_model_with_spinner(model, window(info) if window else None)
    return info


def _static_info(model: str) -> dict:
    info = model_info(model)  # /api/show: works whether or not the model is loaded
    details = info.get("details", {})
    model_arch_info = info.get("model_info", {})
    architecture = details.get("family", "?")
    context_window = model_arch_info.get(f"{architecture}.context_length")
    embedding_length = model_arch_info.get(f"{architecture}.embedding_length")
    supports_thinking = "thinking" in info.get("capabilities", [])
    return {
        "parameter_size": details.get("parameter_size", "?"),
        "quantization_level": details.get("quantization_level", "?"),
        "family": details.get("family", "?"),
        "supports_thinking": supports_thinking,
        "context_window": context_window,
        "embedding_length": embedding_length,
    }


def chat(
    model: str,
    messages: list[dict],
    num_ctx: int = 8192,
    think: bool | str = True,
    tools: list[dict] | None = None,
    fmt: dict | None = None,
    options: dict | None = None,
):
    """Yields text chunks, then a dict with tool_calls (if the model made any), then a dict with its
    thinking (if it did any), then a final usage dict. While the model reasons, each piece of its
    thinking also comes as it arrives, as {"thinking_chunk": ...}, so a caller can watch it; closing
    the generator early closes the request, which stops the model.

    `think` is True/False, or a reasoning level ("low", "medium", "high") for models that
    take one. It is ignored by models that don't support reasoning/thinking mode.

    `fmt` is a JSON schema the reply must match (Ollama's `format`): the model can then only emit
    JSON that fits it. Thinking models think first; the schema applies to the reply.

    `options` are sampling settings for this request (temperature, top_p, ...), on top of the
    model's Modelfile PARAMETERs.
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": think,
        "options": {**(options or {}), "num_ctx": num_ctx},
        **_keep_alive(),
    }
    if tools:
        payload["tools"] = tools
    if fmt is not None:
        payload["format"] = fmt

    r = requests.post(
        f"{BASE}/api/chat",
        json=payload,
        stream=True,
        timeout=(5, CHAT_READ_TIMEOUT_SECONDS),   # connect, read
    )
    r.raise_for_status()

    thinking = ""
    try:
        for line in r.iter_lines():
            if not line:
                continue
            obj = json.loads(line)
            message = obj.get("message", {})
            if chunk := message.get("content"):
                yield chunk
            if thought := message.get("thinking"):
                thinking += thought
                yield {"thinking_chunk": thought}
            if tool_calls := message.get("tool_calls"):
                yield {"tool_calls": tool_calls}
            if obj.get("done"):
                if thinking:
                    yield {"thinking": thinking}
                yield {
                    "prompt_tokens": obj.get("prompt_eval_count", 0),
                    "completion_tokens": obj.get("eval_count", 0),
                    "stop_reason": obj.get("done_reason"),
                }
    finally:
        r.close()  # an abandoned stream stops the model, rather than letting it run on unread
