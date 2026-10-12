"""The real nuru-serve host and HTTP server over a scripted Session: no model, no MCP, no vault writes. For
end-to-end tests of clients (the Obsidian plugin's, WP01 M3) and for trying a client by hand.

    uv run --locked python nuru/tests/scripted_serve.py --dir <scratch> [--port 0] [--answer-seconds 10]

It makes <scratch>/NuruPluginTestVault (plugin_test_vault.py), its databases and a client token under <scratch>, then
prints one JSON line: {"port", "token", "vault", "vault_id"}. A turn's text picks its scenario (SCENARIOS);
any other text is echoed back. Stop it with SIGTERM or Ctrl-C.
"""

import argparse
import json
import secrets
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core import questions  # noqa: E402
from core.conversation_store import ConversationStore  # noqa: E402
from core.service.credentials import Credentials  # noqa: E402
from core.service.host import Service  # noqa: E402
from core.service.server import NuruServer  # noqa: E402
from core.service.store import ServiceStore  # noqa: E402
from core.session import (Ask, Failure, RetryWithoutThinking, Steered, Text, Thinking,  # noqa: E402
                          ThinkingChunk, ToolCall)
from core.tool_dispatch import MUTATIONS, ToolOutcome, failure  # noqa: E402
from plugin_test_vault import NOTES, build  # noqa: E402

HOSTILE = ('Here is what the note says:\n\n<script>alert("x")</script>\n<img src=x onerror="alert(1)">\n'
           '[click](javascript:alert(1)) and [[nuru/Trip.md]].\n\n<b onclick="alert(2)">bold</b>')


class ScenarioSession:
    """Stands in for Session: each turn runs the scenario its text names, and reports tool outcomes
    through on_tool_outcome as Session does."""

    def __init__(self):
        self.conversation_id = None
        self.answer_question = None
        self.on_tool_outcome = None

    def use_conversation(self, conversation_id):
        self.conversation_id = conversation_id

    def close(self):
        pass

    def ask(self, text, cancel=None):
        scenario = SCENARIOS.get(text.split("\n\n", 1)[0].strip())
        if scenario is None:
            yield Text(f"You said: {text}")
            return
        yield from scenario(self, cancel)

    def tool(self, name, arguments, outcome):
        call = ToolCall(name, arguments, secrets.token_urlsafe(12))
        yield call
        self.outcome(call, outcome)

    def outcome(self, call, outcome):
        if self.on_tool_outcome:
            self.on_tool_outcome(call.call_id, call.name, call.arguments, outcome, call.name in MUTATIONS)


def completed(session, cancel):
    yield Text("Your trip is in **May**, see [[nuru/Trip.md]].\n\n")
    yield Text("```\nbudget = 1200\n```\n")


def tools(session, cancel):
    yield from session.tool("search_notes", {"query": "trip"}, ToolOutcome("nuru/Trip.md", True))
    yield from session.tool("read_note", {"name": "nuru/Trip.md"}, ToolOutcome(NOTES["nuru/Trip.md"], True))
    yield from session.tool("write_note", {"note": "Summary", "content": "Rome in May."},
                            ToolOutcome(json.dumps({"done": True, "path": "nuru/summary.md", "id": None, "created": True}), True))
    yield Text("I read [[nuru/Trip.md]] and wrote [[nuru/summary.md]].")


def refused_write(session, cancel):
    yield from session.tool("write_note", {"note": "shared/Team.md", "content": "x"},
                            failure("policy", "NOT_RUN", "write_note was not run: the user didn't allow it."))
    yield Text("I didn't change shared/Team.md.")


def uncertain_write(session, cancel):
    yield from session.tool("write_note", {"note": "nuru/Summary.md", "content": "x"},
                            failure("transport", "TimeoutError", "no answer", certainty="uncertain"))
    yield Failure("The outcome of write_note is uncertain. Do not retry the write.")


def approval(session, cancel):
    """Asks before a write, as Session does for shared/, and writes only on Yes."""
    call = ToolCall("write_note", {"note": "shared/Team.md", "content": "Update"}, secrets.token_urlsafe(12))
    yield call
    answer = session.answer_question(questions.shared_write("write_note", call.arguments, "shared/Team.md"))
    outcome = (ToolOutcome(json.dumps({"done": True, "path": "shared/Team.md", "id": None, "created": False}), True)
               if questions.is_yes(answer) else failure("policy", "NOT_RUN", "write_note was not run: the user didn't allow it."))
    session.outcome(call, outcome)
    yield Text("Done: updated [[shared/Team.md]]." if outcome.success else "I left shared/Team.md as it was.")


def choice(session, cancel):
    answer = session.answer_question(questions.Question(
        "Which note should I summarise?", questions.SINGLE,
        (questions.Option("nuru/Trip.md", recommended=True), questions.Option("user/Budget.md")), allow_other=True,
        timeout=questions.ANSWER_SECONDS))
    yield Text(f"You chose: {questions.answer_text(answer) if answer else 'nothing'}.")


def failed(session, cancel):
    yield Text("Starting...")
    yield Failure("Nuru couldn't finish: the notes server stopped.")


def slow(session, cancel):
    """Streams until stopped: for Stop and for the queue."""
    for i in range(600):
        if cancel is not None and cancel.wait(0.1):
            return
        yield Text(f"{i} ")


def asks_user(session, cancel):
    yield Ask(questions.yes_no("Shall I also update the budget?", recommend="No"))


def hostile(session, cancel):
    yield Text(HOSTILE)


def long_reply(session, cancel):
    for i in range(400):
        yield Text(f"Line {i}: " + "lorem ipsum " * 15 + "\n")


def activity(session, cancel):
    """What the panel shows of a turn's steps (WP01-ACT): streamed thinking, a call that works, one that
    fails, a steer and a silent retry, then the reply. Pauses so the live line can be seen."""
    def pause(seconds=1.5):
        return cancel is not None and cancel.wait(seconds)

    words = "The user wants today's tasks as checkboxes. I'll read the daily note first. "
    for word in words.split(" "):
        yield ThinkingChunk(word + " ")
        if pause(0.1):
            return
    yield Thinking(words)
    yield from session.tool("read_note", {"name": "nuru/daily-2026-10-11.md"}, failure("tool", "NOT_FOUND", "no such note"))
    if pause():
        return
    yield ThinkingChunk("The note isn't there. Search for it instead, and think it over at length...")
    if pause():
        return
    yield Steered("over budget")
    yield from session.tool("search_notes", {"query": "daily 2026-10-11"}, ToolOutcome("nuru/Trip.md", True))
    if pause():
        return
    yield Thinking("Only thinking, no answer.")
    yield RetryWithoutThinking()
    if pause():
        return
    yield Text("I couldn't find today's daily note, so I changed nothing.")


def bench(session, cancel):
    """For the WP01 benchmark: 20 chunks 5 ms apart, each stamped ⟦t=…⟧ with the wall-clock ms it was
    yielded at, so a client can tell how long each took to reach it."""
    for i in range(20):
        if cancel is not None and cancel.wait(0.005):
            return
        yield Text(f"⟦t={time.time() * 1000:.3f}⟧ chunk {i}\n\n")


SCENARIOS = {
    "scenario:completed": completed, "scenario:tools": tools, "scenario:refused-write": refused_write,
    "scenario:uncertain-write": uncertain_write, "scenario:approval": approval, "scenario:choice": choice,
    "scenario:failed": failed, "scenario:slow": slow, "scenario:ask": asks_user, "scenario:hostile": hostile,
    "scenario:long": long_reply, "scenario:activity": activity, "scenario:bench": bench,
}


def start(directory: Path, port: int = 0, answer_seconds: float | None = None):
    """(server, service, token) for a scripted nuru-serve in `directory`; the caller runs serve_forever."""
    vault = build(directory)
    credentials = Credentials(directory / "service")
    token = credentials.add("obsidian")
    session = ScenarioSession()
    service = Service(ServiceStore(directory / "service.db"), ConversationStore(directory / "conversation.db"),
                      lambda conversation_id: session, vault, answer_seconds=answer_seconds)
    return NuruServer(service, credentials, port, keepalive_seconds=1), service, token


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--answer-seconds", type=float, default=None)
    args = parser.parse_args(argv)
    args.dir.mkdir(parents=True, exist_ok=True)
    server, service, token = start(args.dir, args.port, args.answer_seconds)
    print(json.dumps({"port": server.server_port, "token": token, "vault": str(service.vault),
                      "vault_id": service.vault_id}), flush=True)
    stopped = threading.Event()

    def stop(*_):
        stopped.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.serve_forever()
    server.closing = True
    service.close(timeout=5)
    server.server_close()


if __name__ == "__main__":
    main()
