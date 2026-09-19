"""Thin client for a local Ollama server (https://ollama.com), used to spin up
the server and a model on demand and stream chat responses from it.
"""

import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import requests

BASE = "http://localhost:11434"

# Placeholder in the Modelfile template that create_model() substitutes with
# the actual base model to build from.
_BASE_MODEL_PLACEHOLDER = "__BASE_MODEL__"

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


def create_model(model: str, base_model: str, modelfile_path: Path) -> None:
    """(Re)builds `model` in Ollama from the Modelfile template at `modelfile_path`,
    with its __BASE_MODEL__ placeholder filled in with `base_model`.

    `ollama create` overwrites an existing model of the same name, so this is
    safe (and intended) to call on every startup - it keeps `model`'s baked-in
    system prompt in sync with the template without ever needing a manual
    `ollama create` step.
    """
    rendered = modelfile_path.read_text().replace(_BASE_MODEL_PLACEHOLDER, base_model)
    with tempfile.NamedTemporaryFile("w", suffix=".Modelfile", delete=False) as f:
        f.write(rendered)
        rendered_path = f.name
    try:
        subprocess.run(["ollama", "create", model, "-f", rendered_path], check=True, capture_output=True, text=True)
    finally:
        os.remove(rendered_path)


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


def _load_model_with_spinner(model: str) -> None:
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
        r = requests.post(f"{BASE}/api/generate", json={"model": model}, timeout=300)
        r.raise_for_status()
    finally:
        done.set()
        spinner.join()
        print("\r" + " " * (len(model) + 20) + "\r", end="", flush=True)


def ensure_model_loaded(model: str) -> dict:
    """Load `model` into memory first if it isn't resident yet.

    Returns its static info (parameter_size, quantization_level, family, supports_thinking).
    """
    if not is_model_loaded(model):
        _load_model_with_spinner(model)

    info = model_info(model)
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


def chat(model: str, messages: list[dict], num_ctx: int = 8192, think: bool = True, tools: list[dict] | None = None):
    """Yields text chunks, then a dict with tool_calls (if the model made any), then a final usage dict.

    `think` is ignored by models that don't support reasoning/thinking mode.
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": think,
        "options": {"num_ctx": num_ctx},
    }
    if tools:
        payload["tools"] = tools

    r = requests.post(
        f"{BASE}/api/chat",
        json=payload,
        stream=True,
        timeout=(5, CHAT_READ_TIMEOUT_SECONDS),   # connect, read
    )
    r.raise_for_status()

    for line in r.iter_lines():
        if not line:
            continue
        obj = json.loads(line)
        message = obj.get("message", {})
        if chunk := message.get("content"):
            yield chunk
        if tool_calls := message.get("tool_calls"):
            yield {"tool_calls": tool_calls}
        if obj.get("done"):
            yield {
                "prompt_tokens": obj.get("prompt_eval_count", 0),
                "completion_tokens": obj.get("eval_count", 0),
                "stop_reason": obj.get("done_reason"),
            }
