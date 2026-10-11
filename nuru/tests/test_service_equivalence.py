"""The same scripted model turn through Session directly and through nuru-serve over HTTP gives the same
reply, the same tool calls in the same order and the same changes to the vault (WP01's session
equivalence lane). A real Session and the real notes server in a fresh NuruPluginTestVault per route;
only the model is scripted. Chunk boundaries and timing may differ; nothing else may."""

import http.client
import json
import os
import re
import signal
import subprocess
import threading

import sys
from pathlib import Path

import pytest
from obsidian_mcp import mutations

import plugin_test_vault
from core import session
from core.conversation_store import ConversationStore
from core.service import protocol
from core.service.credentials import Credentials
from core.service.host import Service
from core.service.server import NuruServer
from core.service.store import ServiceStore
from core.session import Text, ToolCall
from real_writer_serve import TASK, real_session

MESSAGE = "Read my café notes, update Stale.md, write a Rome summary and add tickets to the shared plan."
STALE = "nuru/Stale.md"
OUTSIDE_EDIT = "# Stale\n\nRevision 2, edited in Obsidian.\n"


def contents(vault):
    """Every file's text, with what a new note gets fresh each time (its ULID, its creation minute) masked."""
    return {path: re.sub(r"created: \S+", "created: <time>", re.sub(r"\b[0-9A-HJKMNP-TV-Z]{26}\b", "<id>", (vault / path).read_text()))
            for path in plugin_test_vault.snapshot(vault)}


def call(name, arguments):
    return {"function": {"name": name, "arguments": arguments}}


class ScriptedModel:
    """The model's side of the turn, one response per chat call. Before the replace it edits Stale.md on
    disk, as the user might in Obsidian, so the revision the model read is stale."""

    def __init__(self, vault):
        self.vault, self.calls = vault, 0
        revision = mutations.revision(plugin_test_vault.NOTES[STALE].encode())
        self.responses = [
            [{"tool_calls": [call("read_note", {"name": "nuru/Café ☕ notes.md"}),
                             call("read_note", {"name": STALE, "include_revision": True})]}],
            [{"tool_calls": [call("write_note", {"note": STALE, "mode": "replace", "content": "# Stale\n\nNuru's rewrite.\n",
                                                 "expected_revision": revision})]}],
            [{"tool_calls": [call("write_note", {"note": "Rome summary", "content": "Rome in May; budget 1200 EUR.\n"})]}],
            [{"tool_calls": [call("write_note", {"note": "shared/Plan.md", "content": "- [ ] buy tickets\n"})]}],
            ["Done. Stale.md changed since I read it, so I left it. ", "See [[nuru/Café ☕ notes.md]]."],
        ]

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 2:
            (self.vault / STALE).write_text(OUTSIDE_EDIT)
        response = self.responses[min(self.calls, len(self.responses)) - 1]
        yield from [*response, {"prompt_tokens": 1, "completion_tokens": 1}]


@pytest.fixture
def route(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "ensure_server_running", lambda: None)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model: None)
    opened = []

    def make(name):
        directory = tmp_path / name
        vault = plugin_test_vault.build(directory)
        model = ScriptedModel(vault)
        monkeypatch.setattr(session, "chat", model)
        s = real_session(vault, directory)
        opened.append(s)
        return directory, vault, s, model

    yield make
    for s in opened:
        s.close()


def direct(directory, vault, s):
    calls, outcomes, reply = [], [], ""
    s.answer_question = lambda question: ["Yes"]
    s.on_tool_outcome = lambda call_id, name, arguments, outcome, write: outcomes.append((name, outcome.success))
    for event in s.ask(MESSAGE):
        if isinstance(event, ToolCall):
            calls.append((event.name, event.arguments))
        elif isinstance(event, Text):
            reply += event.text
    return reply, calls, outcomes


def post(port, headers, path, body):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("POST", path, json.dumps(body), headers)
    answer = json.loads(connection.getresponse().read())
    connection.close()
    return answer


def turn(port, headers, conversation, text, answer, n=1):
    """Submit `text` and follow its live stream to the end, answering each question `answer` as the plugin
    would. Its events."""
    accepted = post(port, headers, f"/v1/conversations/{conversation}/turns", {"request_id": f"req-turn-{n:08d}", "text": text})
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    connection.request("GET", accepted["events"] + "?after=0", headers=headers)
    response, events, block = connection.getresponse(), [], b""
    for line in response:
        block += line
        if line != b"\n":
            continue
        for event in protocol.parse_sse(block):
            events.append(event)
            if event["type"] == "question":
                post(port, headers, f"/v1/questions/{event['data']['question_id']}/answers",
                     {"request_id": f"req-answer-{n:07d}", "operation": event["data"]["operation"], "chosen": [answer]})
        block = b""
        if events and events[-1]["type"] in protocol.TERMINAL:
            break
    connection.close()
    return events


def over_http(directory, vault, s, on_events=None, on_end=None):
    """The turn through nuru-serve over HTTP, answering its question Yes. With on_events, its events go
    there and come back raw; on_end(service) runs before the service closes."""
    credentials = Credentials(directory / "service")
    token = credentials.add("obsidian")
    service = Service(ServiceStore(directory / "service.db"), ConversationStore(directory / "conversation.db"),
                      lambda conversation_id: s, vault)
    server = NuruServer(service, credentials, port=0, keepalive_seconds=0.2)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    headers = {"Host": f"127.0.0.1:{server.server_port}", "Authorization": f"Bearer {token}"}

    try:
        conversation = post(server.server_port, headers, "/v1/conversations", {"request_id": "req-conversation-0001"})["conversation_id"]
        events = turn(server.server_port, headers, conversation, MESSAGE, "Yes")
        if on_end:
            on_end(service)
    finally:
        server.closing = True
        service.close()
        server.shutdown()
        server.server_close()
        service.store.close()
        service.conversations.close()
    protocol.check_stream(events)
    if on_events:
        on_events(events)
        return events
    assert events[-1]["type"] == "completed"
    names = {e["data"]["call_id"]: e["data"]["name"] for e in events if e["type"] == "tool_start"}
    calls = [(e["data"]["name"], e["data"]["arguments"]) for e in events if e["type"] == "tool_start"]
    outcomes = [(names[e["data"]["call_id"]], e["data"]["ok"]) for e in events if e["type"] == "tool_result"]
    return events[-1]["data"]["reply"], calls, outcomes


def test_direct_session_and_http_give_the_same_turn_and_the_same_vault(route):
    results = {}
    for name, run in (("direct", direct), ("http", over_http)):
        directory, vault, s, model = route(name)
        before = plugin_test_vault.snapshot(vault)
        reply, calls, outcomes = run(directory, vault, s)
        after = plugin_test_vault.snapshot(vault)
        text = contents(vault)
        store = ConversationStore(directory / "conversation.db")  # the service has closed Session's
        stored = [(m["role"], m["content"]) for m in store.get_recent_messages(s.conversation_id, 100_000)]
        store.close()
        results[name] = (reply, calls, outcomes, plugin_test_vault.changes(before, after), text, model.calls, stored)
        assert (vault / STALE).read_text() == OUTSIDE_EDIT  # the stale replace was refused, the outside edit kept

    assert results["direct"][:4] == results["http"][:4]
    reply, calls, outcomes, changed, text, model_calls, stored = results["direct"]
    assert [name for name, _ in calls] == ["read_note", "read_note", "write_note", "write_note", "write_note"]
    assert outcomes == [("read_note", True), ("read_note", True), ("write_note", False), ("write_note", True), ("write_note", True)]
    assert changed == {"added": ["nuru/rome-summary.md"], "changed": ["nuru/Stale.md", "shared/Plan.md"], "removed": []}
    assert text == results["http"][4]  # the same text, file for file, but a new note's ID and time
    assert model_calls == results["http"][5] == 5
    assert stored == results["http"][6] and stored[-1] == ("nuru", reply)
    assert reply.endswith("[[nuru/Café ☕ notes.md]].")


def children(pid):
    found = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    return [int(child) for child in found] + [grandchild for child in found for grandchild in children(int(child))]


def test_a_notes_server_that_dies_mid_turn_leaves_its_write_uncertain_and_the_service_answering(route):
    """MCP disconnect: the notes server is killed between the model's read and its write. The write is
    reported uncertain and never retried, the vault is untouched, and status still answers."""
    directory, vault, s, model = route("killed")
    notes_server = children(os.getpid())
    assert notes_server

    def kill_then_respond(*args, **kwargs):
        model.calls += 1
        if model.calls == 2:
            for pid in notes_server:
                os.kill(pid, signal.SIGKILL)
        yield from [*model.responses[0 if model.calls == 1 else 2], {"prompt_tokens": 1, "completion_tokens": 1}] \
            if model.calls <= 2 else ["I couldn't finish.", {"prompt_tokens": 1, "completion_tokens": 1}]

    session.chat = kill_then_respond
    before = plugin_test_vault.snapshot(vault)
    receipts, status = [], {}

    def follow(events):
        receipts.extend(e["data"]["state"] for e in events if e["type"] == "write_receipt")

    events = over_http(directory, vault, s, on_events=follow, on_end=lambda service: status.update(service.status()))
    assert receipts == ["uncertain"]
    assert [e["data"]["name"] for e in events if e["type"] == "tool_start"] == ["read_note", "read_note", "write_note"]
    assert events[-1]["type"] == "failed"  # Session: "the outcome of write_note is uncertain"
    assert plugin_test_vault.snapshot(vault) == before
    assert status["ready"]["chat"] == "ready"


def test_the_real_writer_server_writes_on_yes_and_leaves_the_vault_on_no(tmp_path):
    """real_writer_serve.py, as the manual byte check runs it: a real process. No changes nothing and gets a
    refused receipt; Yes changes exactly shared/Plan.md and gets an applied receipt naming it."""
    script = Path(__file__).with_name("real_writer_serve.py")
    process = subprocess.Popen([sys.executable, str(script), "--dir", str(tmp_path)], stdout=subprocess.PIPE, text=True)
    try:
        started = json.loads(process.stdout.readline())
        vault = Path(started["vault"])
        headers = {"Host": f"127.0.0.1:{started['port']}", "Authorization": f"Bearer {started['token']}"}
        conversation = post(started["port"], headers, "/v1/conversations", {"request_id": "req-conversation-0001"})["conversation_id"]
        receipts = []
        for n, answer in enumerate(("No", "Yes"), start=1):
            before = plugin_test_vault.snapshot(vault)
            events = turn(started["port"], headers, conversation, "Add the task.", answer, n)
            protocol.check_stream(events)
            receipts.append([(e["data"]["state"], e["data"].get("path")) for e in events if e["type"] == "write_receipt"])
            changed = plugin_test_vault.changes(before, plugin_test_vault.snapshot(vault))
            assert changed == ({"added": [], "changed": [], "removed": []} if answer == "No"
                               else {"added": [], "changed": ["shared/Plan.md"], "removed": []})
        assert receipts == [[("refused", None)], [("applied", "shared/Plan.md")]]
        assert (vault / "shared/Plan.md").read_text().endswith(TASK)
    finally:
        process.terminate()
        process.wait(10)
