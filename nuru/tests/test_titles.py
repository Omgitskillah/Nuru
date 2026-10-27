"""core.titles against a stand-in Ollama on a loopback port that streams /api/generate as Ollama does
(chunked, a JSON object a line): what it is sent, what of its answer becomes the title, and that one
deadline, or stop(), ends it however Ollama stalls."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import titles


class FakeOllama:
    """/api/generate runs `steps`: ("chunk", dict) streams a line, ("hold",) stalls until the client goes
    or the test ends, ("trickle",) streams an empty line every 50 ms until then, ("raw", bytes) streams
    bytes as they are. `status` other than 200 answers that. The headers go out with the first step, as
    Ollama sends them with its first chunk. `framing`: "chunked", as Ollama; "close", HTTP/1.1 with
    Connection: close and no chunking; "http10", an HTTP/1.0 reply."""

    def __init__(self, steps, status=200, framing="chunked"):
        self.steps, self.status, self.framing = steps, status, framing
        self.generated = []
        self.gone = threading.Event()  # the client cut the connection while a reply was streaming
        self.release = threading.Event()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0" if framing == "http10" else "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                fake.generated.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
                if fake.status != 200:
                    self.send_response(fake.status)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                started = False
                chunked = fake.framing == "chunked"

                def send(data):
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data) if chunked else data)
                    self.wfile.flush()

                def start():
                    self.send_response(200)
                    self.send_header("content-type", "application/x-ndjson")
                    self.send_header("transfer-encoding" if chunked else "connection", "chunked" if chunked else "close")
                    self.end_headers()

                try:
                    for step in fake.steps:
                        if step[0] == "hold":
                            self.connection.settimeout(0.02)
                            while not fake.release.is_set():
                                try:
                                    if self.connection.recv(1) == b"":  # the client is gone
                                        fake.gone.set()
                                        return
                                except TimeoutError:
                                    pass
                            return
                        if not started:
                            start()
                            started = True
                        if step[0] == "trickle":
                            while not fake.release.wait(0.05):
                                send(json.dumps({"response": "", "done": False}).encode() + b"\n")
                            return
                        send(step[1] if step[0] == "raw" else json.dumps(step[1]).encode() + b"\n")
                    if started and chunked:
                        self.wfile.write(b"0\r\n\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    fake.gone.set()
                self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ollama():
    fakes = []
    def make(*args, **kwargs):
        fakes.append(FakeOllama(*args, **kwargs))
        return fakes[-1]
    yield make
    for fake in fakes:
        fake.close()


def answer(*words, done=True):
    return [("chunk", {"response": word, "done": False}) for word in words] + ([("chunk", {"response": "", "done": True})] if done else [])


def test_the_turns_model_names_the_conversation_at_the_turns_window(ollama, monkeypatch):
    monkeypatch.setenv("NURU_KEEP_ALIVE", "30m")
    fake = ollama(answer("Lisbon", " trip", " plan"))
    named = titles.title("plan my Lisbon trip " + "x" * 5000, "Flights are about 400 EUR.", "nuru", 32768, base=fake.base)
    assert named == "Lisbon trip plan", "the streamed pieces, joined"
    [sent] = fake.generated
    assert sent["model"] == "nuru" and sent["options"]["num_ctx"] == 32768, \
        "the model the turn ran on, at its window: another window would make Ollama reload it"
    assert sent["think"] is False and sent["stream"] is True
    assert sent["keep_alive"] == "30m", "Nuru's keep-alive, so the title doesn't shorten the model's stay"
    assert "two to six words" in sent["system"], "its own instructions, not Nuru's tool prompt"
    assert "plan my Lisbon trip" in sent["prompt"] and "Flights are about 400 EUR." in sent["prompt"]
    assert len(sent["prompt"]) < 4200, "long messages are cut"


@pytest.mark.parametrize("steps", [
    [("hold",)],  # still reading the conversation: no headers yet
    answer("Lisbon", done=False) + [("hold",)],  # stalls halfway through the title
], ids=["before-the-reply", "during-the-reply"])
def test_one_deadline_bounds_it_however_ollama_stalls_and_ollama_is_told(ollama, steps):
    fake = ollama(steps)
    started = time.monotonic()
    assert titles.title("hello", "hi", "nuru", 8192, base=fake.base, seconds=0.4) is None
    assert time.monotonic() - started < 0.4 + 0.3, "the whole of it, not each read"
    assert fake.gone.wait(2), "the connection is cut, which stops Ollama's work on it"


@pytest.mark.parametrize("framing", ["chunked", "close", "http10"])
def test_a_reply_that_never_ends_is_cut_at_the_deadline_and_on_stop_however_it_is_framed(ollama, framing):
    """Astra's probe on #61 (r2): with Connection: close, http.client lets go of the socket once the headers
    are in, so the watcher must keep its own hold on it, or an endless stream holds the worker."""
    fake = ollama([("trickle",)], framing=framing)
    started = time.monotonic()
    assert titles.title("hello", "hi", "nuru", 8192, base=fake.base, seconds=0.3) is None
    assert time.monotonic() - started < 0.3 + 0.3
    assert fake.gone.wait(2)
    started = time.monotonic()
    assert titles.title("hello", "hi", "nuru", 8192, stop=lambda: time.monotonic() - started > 0.1,
                        base=fake.base, seconds=30) is None
    assert time.monotonic() - started < 0.5


@pytest.mark.parametrize("framing", ["close", "http10"])
def test_a_title_comes_through_however_the_reply_is_framed(ollama, framing):
    assert titles.title("hello", "hi", "nuru", 8192, base=ollama(answer("Lisbon", " plan"), framing=framing).base) == "Lisbon plan"


def test_a_connection_that_cant_be_cut_still_ends_at_its_own_timeout(ollama, monkeypatch):
    import socket
    def refuses(self, how):
        raise OSError("not connected")
    monkeypatch.setattr(socket.socket, "shutdown", refuses)
    fake = ollama([("hold",)])
    started = time.monotonic()
    stop = lambda: time.monotonic() - started > 0.1  # noqa: E731  # asks to cut it long before the deadline
    assert titles.title("hello", "hi", "nuru", 8192, stop=stop, base=fake.base, seconds=0.6) is None
    assert 0.5 < time.monotonic() - started < 0.6 + 0.3, "the socket's own timeout is the deadline's length"


def test_stop_ends_it_at_once_well_before_the_deadline(ollama):
    fake = ollama([("hold",)])
    asked = threading.Event()
    stopping = threading.Timer(0.2, asked.set)
    stopping.start()
    started = time.monotonic()
    assert titles.title("hello", "hi", "nuru", 8192, stop=asked.is_set, base=fake.base, seconds=30) is None
    assert time.monotonic() - started < 1
    assert fake.gone.wait(2)


def test_a_rambling_answer_is_read_only_so_far(ollama):
    fake = ollama(answer(*(["word "] * 2000), done=False) + [("hold",)])
    started = time.monotonic()
    assert titles.title("hello", "hi", "nuru", 8192, base=fake.base, seconds=5) is None, "more than 12 words"
    assert time.monotonic() - started < 2, "it stopped reading, not waited for the deadline"


@pytest.mark.parametrize("fake_args", [
    {"steps": answer(" ")},  # an empty title
    {"steps": answer("Lisbon", done=False)},  # the stream ended without finishing
    {"steps": [("raw", b"not json\n")]},
    {"steps": [("raw", b"x" * (titles._LINE_BYTES + 10))]},  # a line too long to be a chunk
    {"steps": answer("Lisbon"), "status": 500},
], ids=["empty", "unfinished", "not-json", "overlong-line", "error-status"])
def test_any_bad_answer_gives_no_title(ollama, fake_args):
    fake = ollama(**fake_args)
    assert titles.title("hello", "hi", "nuru", 8192, base=fake.base, seconds=2) is None


def test_no_title_without_ollama():
    assert titles.title("hello", "hi", "nuru", 8192, base="http://127.0.0.1:9", seconds=1) is None


# ---------------------------------------------------------------------- on Claude
def claude_chat(*parts, error=None, hold=None):
    """Stands in for core.providers.claude.chat: yields `parts` (text, and dicts as the real one adds),
    or waits for `hold`, or raises `error`; records what it was asked."""
    asked = []
    def chat(model, messages, **kwargs):
        asked.append((model, messages, kwargs))
        if hold is not None:
            hold.wait(5)
        if error is not None:
            raise error
        yield from parts
    chat.asked = asked
    return chat


def test_on_claude_the_conversations_model_names_it_without_thinking():
    chat = claude_chat("Lisbon ", "trip plan", {"prompt_tokens": 90, "completion_tokens": 4})
    assert titles.by_claude(chat, "claude-haiku-5-5", "plan my Lisbon trip " + "x" * 5000, "Flights are 400 EUR.") == "Lisbon trip plan"
    [(model, messages, kwargs)] = chat.asked
    assert model == "claude-haiku-5-5" and kwargs == {"think": False}
    assert messages[0]["role"] == "system" and "two to six words" in messages[0]["content"], \
        "its own instructions in place of Nuru's system prompt"
    assert "plan my Lisbon trip" in messages[1]["content"] and "Flights are 400 EUR." in messages[1]["content"]
    assert len(messages[1]["content"]) < 4200


def test_on_claude_a_failed_slow_or_stopped_request_gives_no_title():
    from core.providers.claude import ClaudeError
    assert titles.by_claude(claude_chat(error=ClaudeError("overloaded")), "claude-haiku-5-5", "hi", "hello") is None
    hold = threading.Event()
    try:
        started = time.monotonic()
        assert titles.by_claude(claude_chat("Late", hold=hold), "claude-haiku-5-5", "hi", "hello", seconds=0.3) is None
        assert time.monotonic() - started < 0.6, "the deadline, though the request can't be cut"
        started = time.monotonic()
        assert titles.by_claude(claude_chat("Late", hold=hold), "claude-haiku-5-5", "hi", "hello",
                                stop=lambda: time.monotonic() - started > 0.1, seconds=30) is None
        assert time.monotonic() - started < 0.5
    finally:
        hold.set()


@pytest.mark.parametrize("answer, title", [
    ('"Lisbon trip plan"', "Lisbon trip plan"),
    ("Title: Decoupling capacitor tips.", "Decoupling capacitor tips"),
    ("# **Weekly review**\nBecause the user asked...", "Weekly review"),
    ("\n\n  Rust   borrow  checker  \n", "Rust borrow checker"),
    ("“Tagebuch Ideen”", "Tagebuch Ideen"),
    ("A " + "verylongword " * 9 + "end", "A " + " ".join(["verylongword"] * 6) + "…"),  # cut at a word, within 80
    ("", None),
    ("...", None),
])
def test_clean_keeps_only_a_title(answer, title):
    assert titles.clean(answer) == title
