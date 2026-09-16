"""Thin client for a local Ollama server (https://ollama.com), used to spin up
the server and a model on demand and stream chat responses from it.
"""

import json
import subprocess
import time

import requests

BASE = "http://localhost:11434"

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


def ensure_model_loaded(model: str) -> dict:
    """Load `model` into memory first if it isn't resident yet.

    Returns its static info (parameter_size, quantization_level, family, supports_thinking).
    """
    if not any(m["name"] == model for m in loaded_models()):
        r = requests.post(f"{BASE}/api/generate", json={"model": model}, timeout=300)
        r.raise_for_status()

    info = model_info(model)
    details = info.get("details", {})
    supports_thinking = "thinking" in info.get("capabilities", [])
    print(
        f"{model}: {details.get('parameter_size', '?')} params, "
        f"{details.get('quantization_level', '?')}, family={details.get('family', '?')}, "
        f"supports_thinking={supports_thinking}"
    )
    return {
        "parameter_size": details.get("parameter_size", "?"),
        "quantization_level": details.get("quantization_level", "?"),
        "family": details.get("family", "?"),
        "supports_thinking": supports_thinking,
    }


def chat(model: str, messages: list[dict], num_ctx: int = 8192, think: bool = True, tools: list[dict] | None = None):
    """Yields text chunks, then a dict with tool_calls (if the model made any), then a final usage dict.

    `think` is ignored by models that don't support reasoning/thinking mode.
    """
    for message in messages:
        print(f"[{message['role']}] {message.get('content', '')}")

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
        timeout=(5, 300),   # connect, read
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
