"""A conversation's title: a few words the local model writes from its first message and reply, for the
history list, in place of the message's first 60 characters that a client names it with.

Only a chat model Ollama already has loaded writes it: none is loaded just for a title, and nothing goes
to Claude for one. With none (Claude as the main model, say), or any failure, there is no title and the
conversation keeps the one it has.
"""

import re

import requests

from core.providers.ollama import BASE, _keep_alive

TIMEOUT_SECONDS = 8  # it runs before the turn is reported finished: never hold that up for long
MAX_CHARS = 80
_SYSTEM = ("You name conversations. Reply with a title of two to six words that says what the conversation "
           "is about, in the language of the user's message. Only the title: no quotes, no full stop.")
_INPUT_CHARS = 2000  # of the message and of the reply: plenty to name it by


def title(message: str, reply: str, *, base: str = BASE, timeout: float = TIMEOUT_SECONDS) -> str | None:
    """A short title for the conversation that `message` and `reply` begin, or None."""
    try:
        model = _loaded_chat_model(base, timeout)
        if model is None:
            return None
        response = requests.post(f"{base}/api/generate", json={
            "model": model, "system": _SYSTEM, "stream": False, "think": False,
            "prompt": f"The user said:\n{message[:_INPUT_CHARS]}\n\nThe assistant replied:\n{reply[:_INPUT_CHARS]}",
            "options": {"num_predict": 24, "temperature": 0.2}, **_keep_alive(),
        }, timeout=timeout)
        response.raise_for_status()
        return clean(response.json().get("response", ""))
    except (requests.RequestException, ValueError):
        return None


def _loaded_chat_model(base: str, timeout: float) -> str | None:
    """The first model Ollama has in memory that writes text (not an embedding model), or None."""
    loaded = requests.get(f"{base}/api/ps", timeout=timeout).json().get("models", [])
    for entry in loaded:
        shown = requests.post(f"{base}/api/show", json={"model": entry["name"]}, timeout=timeout).json()
        if "completion" in (shown.get("capabilities") or []):
            return entry["name"]
    return None


def clean(text: str) -> str | None:
    """The model's answer as a title: its first line, without the quotes, Markdown or label a small model
    adds, at most MAX_CHARS; None if nothing title-like is left."""
    line = next((line for line in text.splitlines() if line.strip()), "")
    line = re.sub(r"^\s*(?:#+\s*|\*\*|title\s*:\s*)", "", line, flags=re.IGNORECASE)
    line = line.strip().strip("\"'`*“”‘’").strip().rstrip(".!:;,").strip()
    line = re.sub(r"\s+", " ", line)
    if not line or len(line.split()) > 12:
        return None
    return line if len(line) <= MAX_CHARS else line[:MAX_CHARS].rsplit(" ", 1)[0] + "…"
