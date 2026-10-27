"""A conversation's title: a few words the local model writes from its first message and reply, for the
history list, in place of the message's first 60 characters that a client names it with.

The model the turn just ran on writes it: a local one at the window that turn asked for, so it stays in
memory as it is (a different window would make Ollama reload it); Claude, if the conversation runs on
Claude (by_claude), as choosing Claude already sends it what the conversation holds. It runs after the
turn has ended (core.service.host), gives way to the next turn, and has one deadline for the whole of
it; anything that goes wrong means no title, and the conversation keeps the one it has.
"""

import http.client
import json
import re
import socket
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

import requests

from core.providers.ollama import BASE, _keep_alive

SECONDS = 8  # all of it: connecting, the model reading the conversation, and writing the title
MAX_CHARS = 80
_SYSTEM = ("You name conversations. Reply with a title of two to six words that says what the conversation "
           "is about, in the language of the user's message. Only the title: no quotes, no full stop.")
_INPUT_CHARS = 2000  # of the message and of the reply: plenty to name it by
_REPLY_CHARS = 1000  # what is read of the model's answer at most; a title is its first line
_LINE_BYTES = 64 * 1024  # one streamed chunk of Ollama's, at most
_POLL_SECONDS = 0.05  # how often the deadline and stop() are checked
_CLAUDE_TOKENS = 64  # a title's worth, with thinking off
_claude_lock = threading.Lock()
_claude_asking: threading.Thread | None = None  # the Claude title request in this process, if any


def title(message: str, reply: str, model: str, num_ctx: int, *, stop: Callable[[], bool] = lambda: False,
          base: str = BASE, seconds: float = SECONDS) -> str | None:
    """A short title for the conversation that `message` and `reply` begin, by `model` at `num_ctx`, or
    None. Within `seconds` of the call, or as soon as `stop()` (called from another thread) is true, the
    connection is cut, which ends Ollama's work on it too, and there is no title."""
    deadline = time.monotonic() + seconds
    address = urlsplit(base)
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=seconds)
    ended, cut = threading.Event(), threading.Event()
    # The socket itself: the connection lets go of it when a reply says "Connection: close", but the
    # reply's body is still read through it.
    connected: list[socket.socket] = []

    def watch():
        while not ended.wait(_POLL_SECONDS):
            if cut.is_set() or stop() or time.monotonic() >= deadline:
                cut.set()
                if connected:  # none while connecting: cut it once it's there
                    try:
                        connected[0].shutdown(socket.SHUT_RDWR)  # wakes a read in progress
                    except OSError:
                        pass
                    return

    watcher = threading.Thread(target=watch, name="nuru-title-deadline", daemon=True)
    watcher.start()
    try:
        connection.connect()
        connected.append(connection.sock)
        connection.request("POST", "/api/generate", json.dumps({
            "model": model, "system": _SYSTEM, "stream": True, "think": False,
            "prompt": _prompt(message, reply),
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


def by_claude(chat, model: str, message: str, reply: str, *, stop: Callable[[], bool] = lambda: False,
              seconds: float = SECONDS) -> str | None:
    """The same title by Claude: `chat` is core.providers.claude.chat (which logs the request), with
    thinking off and a title's few tokens. Past the `seconds`, or once `stop()` is true, there is no
    title, and the request is ended at its next event by closing its stream. One that sends nothing at
    all can't be ended so: until it does, no other conversation is named by Claude (_claude_asking)."""
    global _claude_asking
    with _claude_lock:
        if stop() or (_claude_asking is not None and _claude_asking.is_alive()):
            return None
        answer, given_up = {}, threading.Event()

        def ask():
            stream = chat(model, [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": _prompt(message, reply)}],
                          think=False, max_tokens=_CLAUDE_TOKENS)
            try:
                parts = []
                for part in stream:
                    if given_up.is_set():
                        break
                    if isinstance(part, str):
                        parts.append(part)
                answer["text"] = "".join(parts)
            except requests.RequestException:  # core.providers.claude.ClaudeError
                pass
            finally:
                stream.close()  # ends the request if it's still going

        _claude_asking = threading.Thread(target=ask, name="nuru-title-claude", daemon=True)
        _claude_asking.start()
        asking = _claude_asking
    deadline = time.monotonic() + seconds
    while asking.is_alive():
        if stop() or time.monotonic() >= deadline:
            given_up.set()
            return None
        asking.join(_POLL_SECONDS)
    return None if given_up.is_set() else clean(answer.get("text", ""))


def _prompt(message: str, reply: str) -> str:
    return f"The user said:\n{message[:_INPUT_CHARS]}\n\nThe assistant replied:\n{reply[:_INPUT_CHARS]}"


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
