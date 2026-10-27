"""core.titles against a stand-in Ollama on a loopback port: which model names a conversation, what it is
sent, and what of its answer becomes the title."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import titles


class FakeOllama:
    """/api/ps lists `loaded`; /api/show gives each one's capabilities; /api/generate answers `answer`."""

    def __init__(self, loaded, capabilities, answer="Lisbon trip plan"):
        self.loaded, self.capabilities, self.answer = loaded, capabilities, answer
        self.generated = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, body):
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.reply({"models": [{"name": name} for name in fake.loaded]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                if self.path == "/api/show":
                    self.reply({"capabilities": fake.capabilities[body["model"]]})
                else:
                    fake.generated.append(body)
                    self.reply({"response": fake.answer})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
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


def test_a_loaded_chat_model_names_the_conversation_from_its_start(ollama, monkeypatch):
    monkeypatch.setenv("NURU_KEEP_ALIVE", "30m")
    fake = ollama(["nomic-embed-text:v1.5", "nuru:latest"],
                  {"nomic-embed-text:v1.5": ["embedding"], "nuru:latest": ["completion", "tools"]})
    assert titles.title("plan my Lisbon trip " + "x" * 5000, "Flights are about 400 EUR.", base=fake.base) == "Lisbon trip plan"
    [sent] = fake.generated
    assert sent["model"] == "nuru:latest", "the embedding model never writes it"
    assert sent["think"] is False and sent["stream"] is False
    assert sent["keep_alive"] == "30m", "Nuru's keep-alive, so the title doesn't shorten the model's stay"
    assert "two to six words" in sent["system"], "its own instructions, not Nuru's tool prompt"
    assert "plan my Lisbon trip" in sent["prompt"] and "Flights are about 400 EUR." in sent["prompt"]
    assert len(sent["prompt"]) < 4200, "long messages are cut"


def test_no_title_without_a_loaded_chat_model_or_without_ollama(ollama):
    fake = ollama(["nomic-embed-text:v1.5"], {"nomic-embed-text:v1.5": ["embedding"]})
    assert titles.title("hello", "hi", base=fake.base) is None
    assert fake.generated == [], "no model is loaded just for a title"
    assert titles.title("hello", "hi", base="http://127.0.0.1:9", timeout=1) is None


def test_a_rambling_or_empty_answer_gives_no_title(ollama):
    fake = ollama(["nuru:latest"], {"nuru:latest": ["completion"]}, answer="   ")
    assert titles.title("hello", "hi", base=fake.base) is None
    fake.answer = " ".join(["word"] * 20)
    assert titles.title("hello", "hi", base=fake.base) is None


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
