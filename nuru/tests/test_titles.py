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
    or the test ends, ("raw", bytes) streams bytes as they are. `status` other than 200 answers that.
    The headers go out with the first step, as Ollama sends them with its first chunk."""

    def __init__(self, steps, status=200):
        self.steps, self.status = steps, status
        self.generated = []
        self.gone = threading.Event()  # the client cut the connection while a reply was streaming
        self.release = threading.Event()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

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
                            self.send_response(200)
                            self.send_header("content-type", "application/x-ndjson")
                            self.send_header("transfer-encoding", "chunked")
                            self.end_headers()
                            started = True
                        data = step[1] if step[0] == "raw" else json.dumps(step[1]).encode() + b"\n"
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                        self.wfile.flush()
                    if started:
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
