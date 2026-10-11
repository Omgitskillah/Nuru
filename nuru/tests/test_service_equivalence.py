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

import pytest
from obsidian_mcp import mutations

import plugin_test_vault
from core import session
from core.cli_logging import CLILogger
from core.conversation_store import ConversationStore
from core.providers.mcp_tools import MCPTools
from core.service import protocol
from core.service.credentials import Credentials
from core.service.host import Service
from core.service.server import NuruServer
from core.service.store import ServiceStore
from core.session import Session, Text, ToolCall
from core.tool_contract import MODEL_TOOLS

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


def real_session(vault, directory):
    """A Session on the real notes server in `vault`; no decision layer, no other servers."""
    params = session.OBSIDIAN_SERVER_PARAMS
    tools = MCPTools([params.model_copy(update={"env": {**params.env, "VAULT_PATH": str(vault), "OBSIDIAN_SEMANTIC_DB": ""}})],
                     model_tools=MODEL_TOOLS)
    tools.connect()
    server_tools = tools.list_tools()
    store = ConversationStore(directory / "conversation.db")
    s = Session(tools, session._hide_injected_args(server_tools), CLILogger(directory / "logs", "scripted", False, "obsidian-mcp"),
                store, store.create_conversation(), server_tools=server_tools)
    s._vault_scope = str(vault)
    return s


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

    def post(path, body):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        connection.request("POST", path, json.dumps(body), headers)
        answer = json.loads(connection.getresponse().read())
        connection.close()
        return answer

    try:
        conversation = post("/v1/conversations", {"request_id": "req-conversation-0001"})["conversation_id"]
        accepted = post(f"/v1/conversations/{conversation}/turns", {"request_id": "req-turn-00000001", "text": MESSAGE})
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
        connection.request("GET", accepted["events"] + "?after=0", headers=headers)
        response, events, block = connection.getresponse(), [], b""
        for line in response:  # follow the live stream, answering its question as the plugin would
            block += line
            if line != b"\n":
                continue
            for event in protocol.parse_sse(block):
                events.append(event)
                if event["type"] == "question":
                    post(f"/v1/questions/{event['data']['question_id']}/answers",
                         {"request_id": "req-answer-0000001", "operation": event["data"]["operation"], "chosen": ["Yes"]})
            block = b""
            if events and events[-1]["type"] in protocol.TERMINAL:
                break
        connection.close()
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
