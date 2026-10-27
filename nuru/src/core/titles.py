"""A conversation's title: a few words the local model writes from its first message and reply, for the
history list, in place of the message's first 60 characters that a client names it with.

The model the turn just ran on writes it, at the window that turn asked for: that model is in memory and
stays as it is (a different window would make Ollama reload it). Nothing goes to Claude for a title. It
runs after the turn has ended (core.service.host), gives way to the next turn, and has one deadline for
the whole of it; anything that goes wrong means no title, and the conversation keeps the one it has.
"""

import http.client
import json
import re
import socket
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

from core.providers.ollama import BASE, _keep_alive

SECONDS = 8  # all of it: connecting, the model reading the conversation, and writing the title
MAX_CHARS = 80
_SYSTEM = ("You name conversations. Reply with a title of two to six words that says what the conversation "
           "is about, in the language of the user's message. Only the title: no quotes, no full stop.")
_INPUT_CHARS = 2000  # of the message and of the reply: plenty to name it by
_REPLY_CHARS = 1000  # what is read of the model's answer at most; a title is its first line
_LINE_BYTES = 64 * 1024  # one streamed chunk of Ollama's, at most
_POLL_SECONDS = 0.05  # how often the deadline and stop() are checked


def title(message: str, reply: str, model: str, num_ctx: int, *, stop: Callable[[], bool] = lambda: False,
          base: str = BASE, seconds: float = SECONDS) -> str | None:
    """A short title for the conversation that `message` and `reply` begin, by `model` at `num_ctx`, or
    None. Within `seconds` of the call, or as soon as `stop()` (called from another thread) is true, the
    connection is cut, which ends Ollama's work on it too, and there is no title."""
    deadline = time.monotonic() + seconds
    address = urlsplit(base)
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=seconds)
    ended, cut = threading.Event(), threading.Event()

    def watch():
        while not ended.wait(_POLL_SECONDS):
            if cut.is_set() or stop() or time.monotonic() >= deadline:
                cut.set()
                if connection.sock is not None:  # None while connecting: cut it once it's there
                    try:
                        connection.sock.shutdown(socket.SHUT_RDWR)  # wakes a read in progress
                    except OSError:
                        pass
                    return

    watcher = threading.Thread(target=watch, name="nuru-title-deadline", daemon=True)
    watcher.start()
    try:
        connection.request("POST", "/api/generate", json.dumps({
            "model": model, "system": _SYSTEM, "stream": True, "think": False,
            "prompt": f"The user said:\n{message[:_INPUT_CHARS]}\n\nThe assistant replied:\n{reply[:_INPUT_CHARS]}",
            "options": {"num_ctx": num_ctx, "num_predict": 24, "temperature": 0.2}, **_keep_alive(),
        }), {"content-type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            return None
        text = ""
        while len(text) < _REPLY_CHARS:
            line = response.readline(_LINE_BYTES)
            if not line:
                return None  # cut, or it ended without finishing
            chunk = json.loads(line)
            text += chunk.get("response", "")
            if chunk.get("done"):
                break
        return None if cut.is_set() else clean(text)
    except (OSError, http.client.HTTPException, ValueError):
        return None
    finally:
        ended.set()
        watcher.join()
        connection.close()


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
