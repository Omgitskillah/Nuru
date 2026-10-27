"""nuru-serve's host (core.service.host) with a scripted Session: no model, no MCP, no HTTP. Turns that
must wait are held by threading.Events the test releases, never by sleeps."""

import json
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from core import questions
from core.conversation_store import ConversationStore
from core.service import protocol
from core.service.host import Service
from core.service.protocol import Display, ProtocolError
from core.service.store import ServiceBusy, ServiceStore
from core.session import Ask, Failure, Text, ToolCall
from core import tool_dispatch
from core.providers.mcp_tools import ToolResult
from core.service import host
from core.tool_dispatch import MUTATIONS, ToolOutcome, failure

ME, SOMEONE_ELSE = "operator", "intruder"
WAIT = 10  # seconds before a test gives up on an event it expects


class ScriptedSession:
    """Stands in for Session. Each ask() runs the next script: a function (session, text, cancel)
    yielding Session events. Records which conversation each turn ran in."""

    def __init__(self, scripts):
        self.scripts = scripts
        self.conversation_id = None
        self.answer_question = None
        self.on_tool_outcome = None
        self.asked = []  # (conversation_id, text)
        self.answers = []  # what answer_question returned
        self.closed = self.shut_down = False

    def use_conversation(self, conversation_id):
        self.conversation_id = conversation_id

    def ask(self, text, cancel=None):
        self.asked.append((self.conversation_id, text))
        yield from self.scripts.pop(0)(self, text, cancel)

    def close(self):
        self.closed = True

    def shutdown(self, reason):
        self.shut_down = True


def reply(*chunks):
    def script(session, text, cancel):
        for chunk in chunks:
            yield Text(chunk)
    return script


def held(gate, *chunks):
    """Says `chunks`, then waits for `gate` before finishing."""
    def script(session, text, cancel):
        for chunk in chunks:
            yield Text(chunk)
        assert gate.wait(WAIT)
        yield Text(" done")
    return script


def asks(question):
    """Asks `question` mid-turn and replies with the answer it got."""
    def script(session, text, cancel):
        answer = session.answer_question(question)
        session.answers.append(answer)
        yield Text(f"answer: {answer}")
    return script


class Harness:
    def __init__(self, tmp_path, scripts=(), *, factory_error=None, **options):
        self.tmp_path, self.options = tmp_path, options
        self.session = ScriptedSession(list(scripts))
        self.built = []
        self.factory_error = factory_error
        self.vault = tmp_path / "vault"
        self.vault.mkdir(exist_ok=True)
        self.service = self.open()

    def factory(self, conversation_id):
        self.built.append(conversation_id)
        if self.factory_error:
            raise self.factory_error
        self.session.conversation_id = conversation_id
        return self.session

    def open(self) -> Service:
        return Service(ServiceStore(self.tmp_path / "service.db"), ConversationStore(self.tmp_path / "conversation.db"),
                       self.factory, self.vault, **self.options)

    def conversation(self, principal=ME, request_id="req-conversation-0001") -> str:
        status, body = self.service.create_conversation(principal, {"request_id": request_id})
        assert status == 201
        return body["conversation_id"]

    def submit(self, conversation_id, text="hello", request_id="req-turn-00000001", principal=ME, **extra):
        return self.service.submit_turn(principal, conversation_id, {"request_id": request_id, "text": text, **extra})

    def events_until(self, turn_id, done, after=0):
        """The turn's events from `after`, waiting until `done(events)` holds."""
        events, deadline = [], datetime.now() + timedelta(seconds=WAIT)
        while not done(events):
            assert datetime.now() < deadline, f"gave up waiting; got {[e['type'] for e in events]}"
            new, ended = self.service.wait_events(turn_id, after + len(events), 0.5)
            events += new
            if ended and not new and not done(events):
                raise AssertionError(f"turn ended without it; got {[e['type'] for e in events]}")
        return events

    def finished(self, turn_id):
        events = self.events_until(turn_id, lambda e: bool(e) and e[-1]["type"] in protocol.TERMINAL)
        protocol.check_stream(events)
        return events

    def first(self, turn_id, kind):
        return next(e for e in self.first_all(turn_id, kind) if e["type"] == kind)

    def first_all(self, turn_id, kind):
        """The turn's events up to and including its first `kind` event, at least."""
        return self.events_until(turn_id, lambda es: any(e["type"] == kind for e in es))

    def close(self):
        self.service.close()
        self.service.store.close()
        self.service.conversations.close()


@pytest.fixture
def harness(tmp_path):
    made = []

    def make(scripts=(), **options):
        made.append(Harness(tmp_path, scripts, **options))
        return made[-1]

    yield make
    for h in made:
        h.close()


def shown(events) -> Display:
    display = Display()
    for event in events:
        display.apply(event)
    return display


# --------------------------------------------------------------------------- turns
def test_a_turn_streams_valid_events_into_its_own_conversation(harness):
    h = harness([reply("Hello", " there")])
    conversation = h.conversation()
    status, accepted = h.submit(conversation)
    assert status == 202 and accepted["replayed"] is False
    assert accepted["events"] == f"/v1/turns/{accepted['turn_id']}/events"
    events = h.finished(accepted["turn_id"])
    assert [e["type"] for e in events] == ["accepted", "started", "text", "text", "completed"]
    assert events[-1]["data"]["reply"] == "Hello there"
    store_id = h.service.store.conversation(conversation, ME)["store_id"]
    assert h.session.asked == [(store_id, "hello")] and h.built == [store_id]


def test_the_model_loads_on_the_first_turn_not_at_startup(harness):
    h = harness([reply("hi")])
    assert h.built == [] and h.service.status()["ready"]["chat"] == "idle"
    h.finished(h.submit(h.conversation())[1]["turn_id"])
    assert h.service.status()["ready"]["chat"] == "ready"


def test_status_names_the_vault_by_hash_never_by_path(harness):
    h = harness()
    status = h.service.status()
    protocol.validate("status", status)
    assert status["vault"] == {"id": protocol.vault_id(h.vault), "name": "vault"}
    assert str(h.vault) not in json.dumps(status)


def test_one_shared_session_switches_conversation_and_history_stays_apart(harness):
    h = harness([reply("one"), reply("two"), reply("three")])
    first, second = h.conversation(request_id="req-conversation-0001"), h.conversation(request_id="req-conversation-0002")
    turns = [h.submit(first, "a", "req-turn-00000001")[1], h.submit(second, "b", "req-turn-00000002")[1],
             h.submit(first, "c", "req-turn-00000003")[1]]
    for turn in turns:
        h.finished(turn["turn_id"])
    ids = {c: h.service.store.conversation(c, ME)["store_id"] for c in (first, second)}
    assert h.session.asked == [(ids[first], "a"), (ids[second], "b"), (ids[first], "c")]
    assert len(h.built) == 1  # built once, then switched
    assert [t["text"] for t in h.service.conversation(ME, first, None, 50)["turns"]] == ["c", "a"]
    assert [t["display"]["reply"] for t in h.service.conversation(ME, second, None, 50)["turns"]] == ["two"]


def test_a_session_failure_or_question_ends_the_turn(harness):
    question = questions.yes_no("Which way?")
    h = harness([lambda s, t, c: iter([Text("partly"), Failure("No model")]),
                 lambda s, t, c: iter([Ask(question)])])
    conversation = h.conversation()
    failed = h.finished(h.submit(conversation, request_id="req-turn-00000001")[1]["turn_id"])
    assert failed[-1]["data"] == {"code": "turn_failed", "message": "No model"}
    asked = h.finished(h.submit(conversation, request_id="req-turn-00000002")[1]["turn_id"])
    assert asked[-1]["type"] == "completed" and asked[-1]["data"]["ask"]["text"] == "Which way?"


def test_an_error_inside_session_fails_the_turn_without_details(harness):
    def broken(session, text, cancel):
        yield ToolCall("read_note", {"note": "x"})
        raise RuntimeError("secret note content")

    h = harness([broken, reply("still works")])
    conversation = h.conversation()
    events = h.finished(h.submit(conversation, request_id="req-turn-00000001")[1]["turn_id"])
    assert events[-1]["data"]["code"] == "internal" and "secret" not in json.dumps(events)
    assert h.finished(h.submit(conversation, request_id="req-turn-00000002")[1]["turn_id"])[-1]["type"] == "completed"


def test_a_model_that_cant_start_fails_the_turn_and_is_retried_next_time(harness, capsys):
    h = harness([reply("back")], factory_error=OSError("no ollama"))
    conversation = h.conversation()
    events = h.finished(h.submit(conversation, request_id="req-turn-00000001")[1]["turn_id"])
    assert events[-1]["data"]["code"] == "model_unavailable"
    assert "couldn't start the model and tools: OSError: no ollama" in capsys.readouterr().err, "the reason is logged"
    assert h.service.status()["ready"]["chat"] == "unavailable"
    h.factory_error = None
    assert h.finished(h.submit(conversation, request_id="req-turn-00000002")[1]["turn_id"])[-1]["type"] == "completed"
    assert len(h.built) == 2


# --------------------------------------------------------------------------- request IDs
def test_a_retried_submit_replays_the_turn_and_never_starts_another(harness):
    h = harness([reply("once")])
    conversation = h.conversation()
    _, first = h.submit(conversation)
    status, again = h.submit(conversation)
    assert status == 202 and again == {**first, "replayed": True}
    h.finished(first["turn_id"])
    assert h.submit(conversation)[1]["turn_id"] == first["turn_id"]  # after it ended, too
    assert len(h.session.asked) == 1


def test_a_reused_request_id_for_something_else_is_a_conflict(harness):
    h = harness([reply("one")])
    first, second = h.conversation(request_id="req-conversation-0001"), h.conversation(request_id="req-conversation-0002")
    h.finished(h.submit(first)[1]["turn_id"])
    for conversation, text in ((first, "different"), (second, "hello")):
        with pytest.raises(ProtocolError) as refused:
            h.submit(conversation, text)
        assert refused.value.code == "conflict"
    assert len(h.session.asked) == 1


def test_simultaneous_identical_retries_make_one_turn(harness):
    gate = threading.Event()
    h = harness([held(gate)])
    conversation = h.conversation()
    results, start = [], threading.Barrier(8)

    def submit():
        start.wait()
        results.append(h.submit(conversation)[1])

    threads = [threading.Thread(target=submit) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    gate.set()
    assert len({r["turn_id"] for r in results}) == 1
    assert sorted(r["replayed"] for r in results) == [False] + [True] * 7
    h.finished(results[0]["turn_id"])
    assert len(h.session.asked) == 1


def test_a_refused_request_is_not_recorded_so_a_retry_is_judged_afresh(harness):
    gate = threading.Event()
    h = harness([held(gate), reply("later")], queue_limit=0)
    conversation = h.conversation()
    running = h.submit(conversation, request_id="req-turn-00000001")[1]["turn_id"]
    with pytest.raises(ProtocolError) as refused:
        h.submit(conversation, request_id="req-turn-00000002")
    assert refused.value.code == "queue_full" and refused.value.extra == {"retry_after_seconds": 5}
    gate.set()
    h.finished(running)
    assert h.finished(h.submit(conversation, request_id="req-turn-00000002")[1]["turn_id"])[-1]["type"] == "completed"


def test_request_state_tells_a_client_that_lost_the_response(harness):
    gate = threading.Event()
    h = harness([held(gate, "working")])
    conversation = h.conversation()
    turn = h.submit(conversation)[1]["turn_id"]
    h.first(turn, "text")
    state = h.service.request_state(ME, "req-turn-00000001", "submit_turn")
    assert state["state"] == "running" and state["turn_id"] == turn
    gate.set()
    events = h.finished(turn)
    state = h.service.request_state(ME, "req-turn-00000001", "submit_turn")
    protocol.validate("request_state", state)
    assert (state["state"], state["last_seq"], state["conversation_id"]) == ("completed", len(events), conversation)
    created = h.service.request_state(ME, "req-conversation-0001", "create_conversation")
    assert created["state"] == "completed" and created["conversation_id"] == conversation
    for principal, operation in ((SOMEONE_ELSE, "submit_turn"), (ME, "cancel_turn")):
        with pytest.raises(ProtocolError, match="no such request"):
            h.service.request_state(principal, "req-turn-00000001", operation)


# --------------------------------------------------------------------------- ownership
def test_another_principal_finds_nothing(harness):
    h = harness([lambda s, t, c: iter([])])
    conversation = h.conversation()
    turn = h.submit(conversation)[1]["turn_id"]
    h.finished(turn)
    attempts = [
        lambda: h.submit(conversation, principal=SOMEONE_ELSE, request_id="req-turn-00000009"),
        lambda: h.service.conversation(SOMEONE_ELSE, conversation, None, 10),
        lambda: h.service.check_cursor(SOMEONE_ELSE, turn, 0),
        lambda: h.service.cancel_turn(SOMEONE_ELSE, turn, {"request_id": "req-cancel-0000001"}),
    ]
    for attempt in attempts:
        with pytest.raises(ProtocolError) as refused:
            attempt()
        assert refused.value.code == "not_found"
    assert h.service.list_conversations(SOMEONE_ELSE, None, 50)["conversations"] == []
    assert len(h.session.asked) == 1


def test_conversations_are_listed_newest_first_in_pages(harness):
    h = harness()
    made = [h.conversation(request_id=f"req-conversation-000{i}") for i in range(5)]
    first = h.service.list_conversations(ME, None, 2)["conversations"]
    rest = h.service.list_conversations(ME, first[-1]["id"], 50)["conversations"]
    assert [c["id"] for c in first + rest] == made[::-1]


# --------------------------------------------------------------------------- the queue
def test_turns_wait_their_turn_and_status_stays_responsive(harness):
    gate = threading.Event()
    h = harness([held(gate, "first"), reply("second")], queue_limit=1)
    conversation = h.conversation()
    first = h.submit(conversation, request_id="req-turn-00000001")[1]["turn_id"]
    h.first(first, "text")
    second = h.submit(conversation, request_id="req-turn-00000002")[1]["turn_id"]
    assert h.service.status()["ready"]["queue"] == {"active": 1, "waiting": 1, "limit": 1}
    with pytest.raises(ProtocolError) as refused:
        h.submit(conversation, request_id="req-turn-00000003")
    assert refused.value.code == "queue_full"
    queued = h.events_until(second, lambda e: len(e) >= 2)
    assert queued[1] == {**queued[1], "type": "queued", "data": {"position": 1}}
    assert len(h.session.asked) == 1  # one generation at a time
    gate.set()
    assert h.finished(second)[-1]["data"]["reply"] == "second"
    assert h.service.status()["ready"]["queue"] == {"active": 0, "waiting": 0, "limit": 1}


# --------------------------------------------------------------------------- questions
def answer(h, question_event, chosen, request_id="req-answer-0000001", principal=ME, **extra):
    data = question_event["data"]
    return h.service.answer_question(principal, data["question_id"],
                                     {"request_id": request_id, "operation": data["operation"], "chosen": chosen, **extra})


@pytest.mark.parametrize("chosen,decided", [(["Yes"], "Yes"), (["No"], "No")])
def test_an_answered_question_reaches_session_and_closes_in_the_stream(harness, chosen, decided):
    h = harness([asks(questions.write("write_note", {"note": "x"}))])
    turn = h.submit(h.conversation())[1]["turn_id"]
    asked = h.first(turn, "question")
    status, closed = answer(h, asked, chosen)
    assert status == 200 and closed == {"v": 1, "question_id": asked["data"]["question_id"], "outcome": "answered",
                                        "chosen": chosen, "decision": decided}
    events = h.finished(turn)
    assert h.session.answers == [chosen]
    assert shown(events).decisions == [{k: v for k, v in closed.items() if k != "v"}]


def test_an_unanswered_question_expires_on_the_host_as_no(harness):
    h = harness([asks(questions.write("write_note", {"note": "x"}))], answer_seconds=0.2)
    turn = h.submit(h.conversation())[1]["turn_id"]
    asked = h.first(turn, "question")
    events = h.finished(turn)
    closed = next(e for e in events if e["type"] == "question_closed")
    assert closed["data"] == {"question_id": asked["data"]["question_id"], "outcome": "expired", "decision": "No"}
    assert h.session.answers == [None]  # Session takes None as No
    with pytest.raises(ProtocolError) as late:
        answer(h, asked, ["Yes"])
    assert late.value.code == "question_closed"


def test_answers_that_dont_fit_are_refused_and_change_nothing(harness):
    h = harness([asks(questions.write("write_note", {"note": "x"}))])
    turn = h.submit(h.conversation())[1]["turn_id"]
    asked = h.first(turn, "question")
    refusals = [
        (lambda: answer(h, asked, ["Yes"], principal=SOMEONE_ELSE), "not_found"),
        (lambda: answer(h, asked, ["Yes"], operation="0" * 64), "wrong_operation"),
        (lambda: answer(h, asked, ["Maybe"]), "invalid_request"),
        (lambda: answer(h, asked, ["Yes"], other="sure"), "invalid_request"),
    ]
    for attempt, code in refusals:
        with pytest.raises(ProtocolError) as refused:
            attempt()
        assert refused.value.code == code
    assert answer(h, asked, ["No"])[1]["decision"] == "No"
    assert answer(h, asked, ["No"])[1]["decision"] == "No"  # the same request again: replayed
    with pytest.raises(ProtocolError) as second:
        answer(h, asked, ["Yes"], request_id="req-answer-0000002")
    assert second.value.code == "question_closed"
    h.finished(turn)
    assert h.session.answers == [["No"]]


# --------------------------------------------------------------------------- Stop
def test_stop_while_queued_ends_the_turn_before_it_starts(harness):
    gate = threading.Event()
    h = harness([held(gate), reply("never")])
    conversation = h.conversation()
    first = h.submit(conversation, request_id="req-turn-00000001")[1]["turn_id"]
    second = h.submit(conversation, request_id="req-turn-00000002")[1]["turn_id"]
    assert h.service.cancel_turn(ME, second, {"request_id": "req-cancel-0000001"})[0] == 202
    events = h.finished(second)
    assert [e["type"] for e in events] == ["accepted", "queued", "cancelled"]
    gate.set()
    h.finished(first)
    assert len(h.session.asked) == 1


def test_stop_while_running_keeps_the_reply_so_far(harness):
    def chatty(session, text, cancel):
        yield Text("so far")
        assert cancel.wait(WAIT)
        yield Text(" and a last word")

    h = harness([chatty])
    turn = h.submit(h.conversation())[1]["turn_id"]
    h.first(turn, "text")
    h.service.cancel_turn(ME, turn, {"request_id": "req-cancel-0000001"})
    events = h.finished(turn)
    assert events[-1]["data"] == {"reason": "user", "partial_reply": "so far and a last word"}
    again = h.service.cancel_turn(ME, turn, {"request_id": "req-cancel-0000002"})
    assert again[0] == 202 and h.service.store.events(turn) == events  # an ended turn: nothing changes


def test_stop_while_a_question_waits_closes_it_as_cancelled_never_yes(harness):
    h = harness([asks(questions.write("write_note", {"note": "x"}))])
    turn = h.submit(h.conversation())[1]["turn_id"]
    asked = h.first(turn, "question")
    h.service.cancel_turn(ME, turn, {"request_id": "req-cancel-0000001"})
    events = h.finished(turn)
    closed = next(e for e in events if e["type"] == "question_closed")
    assert closed["data"] == {"question_id": asked["data"]["question_id"], "outcome": "cancelled", "decision": "No"}
    assert events[-1]["type"] == "cancelled" and h.session.answers == [None]


# --------------------------------------------------------------------------- restart and shutdown
CRASHING_HOST = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from core import questions
from core.conversation_store import ConversationStore
from core.service.host import Service
from core.service.store import ServiceStore
from test_service_host import ScriptedSession, asks

root = Path(sys.argv[1])
session = ScriptedSession([asks(questions.write("write_note", {"note": "x"}))])
service = Service(ServiceStore(root / "service.db"), ConversationStore(root / "conversation.db"),
                  lambda conversation_id: session, root / "vault", answer_seconds=3600)
conversation = service.create_conversation("operator", {"request_id": "req-conversation-0001"})[1]["conversation_id"]
turn = service.submit_turn("operator", conversation, {"request_id": "req-turn-00000001", "text": "hello"})[1]["turn_id"]
service.submit_turn("operator", conversation, {"request_id": "req-turn-00000002", "text": "hello"})
seen = []
while not any(e["type"] == "question" for e in seen):
    seen += service.wait_events(turn, len(seen), 5)[0]
print(conversation, flush=True)
os._exit(9)  # dies with a turn waiting on a question and another queued: no cleanup at all
"""


def test_after_a_crash_unfinished_turns_end_and_are_never_rerun(tmp_path):
    (tmp_path / "vault").mkdir()
    crashed = subprocess.run([sys.executable, "-c", CRASHING_HOST, str(tmp_path), str(Path(__file__).parent)],
                             capture_output=True, text=True, timeout=60)
    assert crashed.returncode == 9, crashed.stderr
    conversation = crashed.stdout.strip()
    session = ScriptedSession([])
    restarted = Service(ServiceStore(tmp_path / "service.db"), ConversationStore(tmp_path / "conversation.db"),
                        lambda conversation_id: session, tmp_path / "vault")
    try:
        turns = [restarted.request_state(ME, f"req-turn-0000000{i}", "submit_turn")["turn_id"] for i in (1, 2)]
        for turn in turns:
            events = restarted.store.events(turn)
            protocol.check_stream(events)
            assert events[-1]["data"]["code"] == "service_restarted"
            for cursor in range(len(events) + 1):  # a client that reconnects from anywhere sees it once
                restarted.check_cursor(ME, turn, cursor)
        closed = next(e for e in restarted.store.events(turns[0]) if e["type"] == "question_closed")
        assert closed["data"]["outcome"] == "expired" and closed["data"]["decision"] == "No"
        assert restarted.request_state(ME, "req-turn-00000001", "submit_turn")["state"] == "failed"
        status, again = restarted.submit_turn(ME, conversation, {"request_id": "req-turn-00000002", "text": "hello"})
        assert again["turn_id"] == turns[1] and again["replayed"] is True
        assert session.asked == []
    finally:
        restarted.close()
        restarted.store.close()


def test_a_second_host_on_the_same_database_is_refused_and_changes_nothing(harness, tmp_path):
    gate = threading.Event()
    h = harness([held(gate, "working")])
    turn = h.submit(h.conversation())[1]["turn_id"]
    h.first(turn, "text")
    before = h.service.store.events(turn)
    with pytest.raises(ServiceBusy):
        h.open()
    assert h.service.store.events(turn) == before
    gate.set()
    assert h.finished(turn)[-1]["type"] == "completed"


def test_shutdown_cancels_turns_in_hand_and_closes_without_stopping_ollama(harness):
    def endless(session, text, cancel):
        yield Text("working")
        assert cancel.wait(WAIT)

    h = harness([endless])
    conversation = h.conversation()
    running = h.submit(conversation, request_id="req-turn-00000001")[1]["turn_id"]
    h.first(running, "text")
    queued = h.submit(conversation, request_id="req-turn-00000002")[1]["turn_id"]
    h.service.close()
    for turn in (running, queued):
        events = h.service.store.events(turn)
        protocol.check_stream(events)
        assert events[-1]["data"]["reason"] == "shutdown"
    assert h.session.closed and not h.session.shut_down
    with pytest.raises(ProtocolError) as refused:
        h.submit(conversation, request_id="req-turn-00000003")
    assert refused.value.code == "shutting_down"


def test_old_turns_keep_their_final_state_after_their_events_are_dropped(harness):
    h = harness([reply("kept")])
    conversation = h.conversation()
    turn = h.submit(conversation)[1]["turn_id"]
    h.finished(turn)
    assert h.service.store.compact(30, now=datetime.now(timezone.utc) + timedelta(days=31)) == 1
    with pytest.raises(ProtocolError) as expired:
        h.service.check_cursor(ME, turn, 0)
    assert expired.value.code == "cursor_expired"
    assert expired.value.extra == {"resync": {"turn": f"/v1/conversations/{conversation}"}}
    (kept,) = h.service.conversation(ME, conversation, None, 10)["turns"]
    assert kept["display"]["reply"] == "kept" and kept["state"] == "completed"


# --------------------------------------------------------------------------- attachments
def test_attachments_are_named_in_the_message_and_cant_leave_the_vault(harness, tmp_path):
    h = harness([reply("ok")])
    (h.vault / "nuru").mkdir()
    (tmp_path / "outside.md").write_text("private")
    os.symlink(tmp_path / "outside.md", h.vault / "nuru" / "link.md")
    conversation = h.conversation()
    with pytest.raises(ProtocolError, match="outside the vault"):
        h.submit(conversation, attachments=[{"path": "nuru/link.md"}])
    turn = h.submit(conversation, request_id="req-turn-00000002",
                    attachments=[{"path": "nuru/Trip.md", "selection": "line one\nline two"}])[1]["turn_id"]
    h.finished(turn)
    assert h.session.asked[0][1] == "hello\n\nAttached note: nuru/Trip.md\n\nSelection:\n> line one\n> line two"


# --------------------------------------------------------------------------- the database
def test_migrations_run_once_and_the_two_databases_join(harness, tmp_path):
    h = harness([reply("hi")])
    conversation = h.conversation()
    h.finished(h.submit(conversation)[1]["turn_id"])
    h.service.conversations.add_message(h.service.store.conversation(conversation, ME)["store_id"], "user", "hello")
    h.close()
    ServiceStore(tmp_path / "service.db").close()  # opening again changes nothing
    with sqlite3.connect(tmp_path / "service.db") as db:
        assert db.execute("SELECT version FROM service_migrations").fetchall() == [(1,), (2,), (3,), (4,)]
        db.execute("ATTACH DATABASE ? AS history", (str(tmp_path / "conversation.db"),))
        joined = db.execute("SELECT c.id, m.content FROM conversations c "
                            "JOIN history.messages m ON m.conversation_id = c.store_id").fetchall()
    assert joined == [(conversation, "hello")]


# --------------------------------------------------------------------------- a change that fails to commit
class FailingCommit:
    """A transaction that rolls back and raises, as a commit hitting a full disk would."""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        self._conn.execute("BEGIN IMMEDIATE")

    def __exit__(self, *raised):
        self._conn.execute("ROLLBACK")
        raise sqlite3.OperationalError("disk I/O error")


def test_a_cancel_that_fails_to_store_leaves_the_queue_working(harness, monkeypatch):
    gate = threading.Event()
    h = harness([held(gate, "first"), reply("second")])
    conversation = h.conversation()
    first = h.submit(conversation, request_id="req-turn-00000001")[1]["turn_id"]
    second = h.submit(conversation, request_id="req-turn-00000002")[1]["turn_id"]

    def failing(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(h.service.store, "add_request", failing)
    with pytest.raises(sqlite3.OperationalError):
        h.service.cancel_turn(ME, second, {"request_id": "req-cancel-0000001"})
    monkeypatch.undo()
    assert [e["type"] for e in h.service.store.events(second)] == ["accepted", "queued"]
    gate.set()
    h.finished(first)
    assert h.finished(second)[-1]["data"]["reply"] == "second"  # still queued, and it ran


def test_an_answer_that_fails_to_commit_is_not_used_and_can_be_sent_again(harness, monkeypatch):
    h = harness([asks(questions.write("write_note", {"note": "x"}))])
    turn = h.submit(h.conversation())[1]["turn_id"]
    asked = h.first(turn, "question")
    store = h.service.store
    real = store.transaction
    monkeypatch.setattr(store, "transaction", lambda: FailingCommit(store._conn))
    with pytest.raises(sqlite3.OperationalError):
        answer(h, asked, ["Yes"])
    monkeypatch.setattr(store, "transaction", real)
    assert store.question(asked["data"]["question_id"], ME)["open"] == 1
    assert h.service._live[turn].stream.open_questions == [asked["data"]["question_id"]]
    assert answer(h, asked, ["No"])[1]["decision"] == "No"
    h.finished(turn)
    assert h.session.answers == [["No"]]


def test_a_commit_refused_by_a_reader_is_rolled_back_and_memory_matches_the_store(harness, tmp_path):
    """Real SQLite: another connection reading the database makes COMMIT fail with "database is locked",
    which leaves the transaction open unless it's rolled back."""
    gate = threading.Event()
    h = harness([held(gate, "first"), reply("second")])
    conversation = h.conversation()
    first = h.submit(conversation, request_id="req-turn-00000001")[1]["turn_id"]
    second = h.submit(conversation, request_id="req-turn-00000002")[1]["turn_id"]
    h.first(first, "text")
    store = h.service.store
    store._conn.execute("PRAGMA busy_timeout = 1")
    reader = sqlite3.connect(tmp_path / "service.db", isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM turns").fetchone()
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        h.service.cancel_turn(ME, second, {"request_id": "req-cancel-0000001"})
    reader.execute("COMMIT")
    assert not store.in_transaction
    committed = [json.loads(body)["type"] for (body,) in reader.execute(
        "SELECT body FROM events WHERE turn_id = ? ORDER BY seq", (second,))]
    assert committed == ["accepted", "queued"] == [e["type"] for e in h.service._live[second].stream.events]
    assert reader.execute("SELECT state FROM turns WHERE id = ?", (second,)).fetchone() == ("accepted",)
    assert reader.execute("SELECT count(*) FROM requests WHERE operation = 'cancel_turn'").fetchone() == (0,)
    reader.close()
    store._conn.execute("PRAGMA busy_timeout = 5000")
    assert h.service.cancel_turn(ME, second, {"request_id": "req-cancel-0000001"})[0] == 202  # judged afresh
    assert h.finished(second)[-1]["data"]["reason"] == "user"
    gate.set()
    assert h.finished(first)[-1]["type"] == "completed"
    assert len(h.session.asked) == 1


class StuckTransaction(FailingCommit):
    """A commit that fails and can't even be rolled back: the transaction stays open."""

    def __exit__(self, *raised):
        raise sqlite3.OperationalError("disk I/O error")


def make_unsafe(h, monkeypatch, conversation, request_id):
    """Submit a turn whose transaction can be neither committed nor rolled back. Returns the SQL the
    service's connection runs from then on."""
    store = h.service.store
    monkeypatch.setattr(store, "transaction", lambda: StuckTransaction(store._conn))
    with pytest.raises(sqlite3.OperationalError):
        h.submit(conversation, request_id=request_id)
    assert store.in_transaction and h.service._unsafe
    traced = []
    store._conn.set_trace_callback(traced.append)
    return traced


def restart(h) -> Service:
    """Stop the unsafe host as nuru-serve does (the connection closes uncommitted) and start another
    on the same files."""
    assert h.service.close(timeout=5)  # promptly: nothing was left waiting
    h.service.store.close()
    h.service.conversations.close()
    h.service = h.open()
    return h.service


@pytest.fixture
def thread_errors(monkeypatch):
    errors = []
    monkeypatch.setattr(threading, "excepthook", errors.append)
    return errors


def test_once_unsafe_nothing_runs_or_is_read_and_a_restart_finds_only_what_committed(harness, monkeypatch, thread_errors):
    gate = threading.Event()
    h = harness([held(gate, "first"), reply("never")])
    conversation = h.conversation()
    running = h.submit(conversation, request_id="req-turn-00000001")[1]["turn_id"]
    h.first(running, "text")
    queued = h.submit(conversation, request_id="req-turn-00000002")[1]["turn_id"]
    traced = make_unsafe(h, monkeypatch, conversation, "req-turn-00000003")

    refused = [
        lambda: h.submit(conversation, request_id="req-turn-00000003"),
        lambda: h.submit(conversation, request_id="req-turn-00000001"),  # not even a replay
        lambda: h.service.request_state(ME, "req-turn-00000003", "submit_turn"),
        lambda: h.service.request_state(ME, "req-turn-00000001", "submit_turn"),
        lambda: h.service.list_conversations(ME, None, 10),
        lambda: h.service.conversation(ME, conversation, None, 10),
        lambda: h.service.check_cursor(ME, running, 0),
        lambda: h.service.wait_events(queued, 0, 1),
        lambda: h.service.cancel_turn(ME, queued, {"request_id": "req-cancel-0000001"}),
    ]
    for attempt in refused:
        with pytest.raises(ProtocolError) as refusal:
            attempt()
        assert refusal.value.code == "internal"
    assert h.service.status()["ready"]["chat"] == "unavailable"  # status reads no database

    gate.set()  # the running turn goes on talking; none of it can be stored
    restarted = restart(h)
    assert len(h.session.asked) == 1  # the queued turn never reached Session
    assert traced == [] and thread_errors == []
    for turn in (running, queued):
        events = restarted.store.events(turn)
        protocol.check_stream(events)
        assert events[-1]["data"]["code"] == "service_restarted"
    assert [e["data"]["text"] for e in restarted.store.events(running) if e["type"] == "text"] == ["first"]
    with pytest.raises(ProtocolError, match="no such request"):
        restarted.request_state(ME, "req-turn-00000003", "submit_turn")


def test_once_unsafe_a_waiting_question_is_woken_with_no_and_nothing_is_written(harness, monkeypatch, thread_errors):
    h = harness([asks(questions.write("write_note", {"note": "x"}))], answer_seconds=3600)
    conversation = h.conversation()
    turn = h.submit(conversation)[1]["turn_id"]
    asked = h.first(turn, "question")
    traced = make_unsafe(h, monkeypatch, conversation, "req-turn-00000002")
    restarted = restart(h)  # well inside the question's hour
    assert h.session.answers == [None] and traced == [] and thread_errors == []
    events = restarted.store.events(turn)
    protocol.check_stream(events)
    closed = next(e for e in events if e["type"] == "question_closed")
    assert closed["data"] == {"question_id": asked["data"]["question_id"], "outcome": "expired", "decision": "No"}
    assert events[-1]["data"]["code"] == "service_restarted"


def test_once_unsafe_a_reader_already_waiting_is_let_go(harness, monkeypatch, thread_errors):
    gate = threading.Event()
    h = harness([held(gate, "first")])
    conversation = h.conversation()
    turn = h.submit(conversation)[1]["turn_id"]
    seen = len(h.first_all(turn, "text"))
    outcome = {}

    def read():
        try:
            outcome["events"] = h.service.wait_events(turn, seen, 30)
        except ProtocolError as error:
            outcome["code"] = error.code

    reader = threading.Thread(target=read)
    reader.start()
    deadline = datetime.now() + timedelta(seconds=WAIT)
    while not h.service._changed._waiters:  # the reader is inside Condition.wait
        assert datetime.now() < deadline
    make_unsafe(h, monkeypatch, conversation, "req-turn-00000002")
    reader.join(5)
    assert not reader.is_alive() and outcome == {"code": "internal"}
    gate.set()
    assert h.service.close(timeout=5) and thread_errors == []


# --------------------------------------------------------------------------- tool results and write receipts
def shown_call(name, arguments):
    return ToolCall(name, arguments, secrets.token_urlsafe(12))


def report(session, call, outcome):
    """What Session does once a shown call has run: tell on_tool_outcome, by the call's ID."""
    session.on_tool_outcome(call.call_id, call.name, call.arguments, outcome, call.name in MUTATIONS)


def calls(*steps):
    """Runs tools as Session does: each (name, arguments, outcome) is shown, then reported."""
    def script(session, text, cancel):
        for name, arguments, outcome in steps:
            call = shown_call(name, arguments)
            yield call
            report(session, call, outcome)
        yield Text("done")
    return script


def written(**result):
    return ToolOutcome(json.dumps(result), True)


def test_each_tool_gets_its_result_and_each_write_a_receipt_of_what_it_did(harness):
    h = harness([calls(
        ("read_note", {"name": "nuru/Trip.md"}, ToolOutcome("Rome", True)),
        ("write_note", {"note": "Trip plan", "content": "x"}, written(path="nuru/trip-plan.md", created=True)),
        ("rename_or_move_file", {"source_path": "nuru/trip-plan.md", "destination_path": "archive"},
         written(**{"from": "nuru/trip-plan.md", "to": "archive/nuru/trip-plan.md"})),
        ("write_note", {"note": "shared/Team.md"}, failure("policy", "NOT_RUN", "the user said No")),
        ("write_note", {"note": "nuru/x.md"}, failure("rejection", "REJECTED_BEFORE_WRITE", "bad", certainty="not_completed")),
        ("write_note", {"note": "nuru/y.md"}, failure("transport", "TimeoutError", "no answer", certainty="uncertain")),
    )])
    events = h.finished(h.submit(h.conversation())[1]["turn_id"])
    started = [e["data"]["call_id"] for e in events if e["type"] == "tool_start"]
    assert [(e["data"]["call_id"], e["data"]["ok"]) for e in events if e["type"] == "tool_result"] == \
        list(zip(started, [True, True, True, False, False, False]))
    receipts = [e["data"] for e in events if e["type"] == "write_receipt"]
    assert receipts == [  # the note each write actually changed, never the name it was asked by
        {"call_id": started[1], "tool": "write_note", "state": "applied", "path": "nuru/trip-plan.md"},
        {"call_id": started[2], "tool": "rename_or_move_file", "state": "applied", "path": "archive/nuru/trip-plan.md"},
        {"call_id": started[3], "tool": "write_note", "state": "refused", "detail": "NOT_RUN"},
        {"call_id": started[4], "tool": "write_note", "state": "failed", "detail": "REJECTED_BEFORE_WRITE"},
        {"call_id": started[5], "tool": "write_note", "state": "uncertain", "detail": "TimeoutError"},
    ]
    assert shown(events).receipts == receipts


REAL_BACKEND = """
import json
from obsidian_mcp import server
created = server.write_note(content="Rome in May.", note="Trip plan")
daily = server.write_note(content="Today.", type="daily")
by_id = server.write_note(content="More.", note=daily["id"])
other = server.write_note(content="x", note="Other")
calls = [
    ("write_note", created),
    ("write_note", daily),
    ("write_note", by_id),
    ("rename_or_move_file", server.rename_or_move_file(source_path=created["path"], destination_path="archive")),
    ("rename_or_move_file", server.rename_or_move_file(source_path=other["path"], destination_path="trash")),
]
print(json.dumps(calls))
"""


def test_receipts_name_the_files_the_real_notes_server_changed(tmp_path):
    """The pinned Obsidian backend, in a scratch vault: a title, a daily, an ID, an archive and a trash
    move. Each applied receipt names a file that exists, whatever the call asked for."""
    vault = tmp_path / "vault"
    for folder in ("nuru", "archive", "trash"):
        (vault / folder).mkdir(parents=True)
    ran = subprocess.run([sys.executable, "-c", REAL_BACKEND], capture_output=True, text=True, timeout=120,
                         env={**os.environ, "VAULT_PATH": str(vault), "OBSIDIAN_SEMANTIC_DB": ""})
    assert ran.returncode == 0, ran.stderr
    paths = []
    for name, result in json.loads(ran.stdout):
        receipt = host._receipt("call00000001", name, tool_dispatch.classify(name, ToolResult(json.dumps(result))))
        assert receipt["state"] == "applied"
        paths.append(receipt["path"])
    assert paths[:3] == ["nuru/trip-plan.md", paths[1], paths[1]] and paths[1].startswith("nuru/daily-")
    assert paths[3:] == ["archive/nuru/trip-plan.md", "trash/nuru/other.md"]
    assert all((vault / path).is_file() for path in paths[1:])  # the first was then archived


def test_an_outcome_is_reported_only_for_its_own_shown_call(harness):
    def script(session, text, cancel):
        read = shown_call("read_note", {"name": "x"})
        yield read
        report(session, read, ToolOutcome("x", True))
        report(session, read, failure("dependency", "SKIPPED", "no"))  # reported already
        write = shown_call("write_note", {"note": "nuru/x.md"})
        yield write
        report(session, shown_call("write_note", {"note": "nuru/y.md"}), ToolOutcome("{}", True))  # never shown
        yield Text("done")

    h = harness([script])
    events = h.finished(h.submit(h.conversation())[1]["turn_id"])
    assert [e["type"] for e in events if e["type"].startswith(("tool", "write"))] == ["tool_start", "tool_result", "tool_start"]


def test_a_huge_write_is_shown_cut_and_the_turn_still_completes(harness):
    content = "x" * 400_000
    h = harness([calls(("write_note", {"note": "nuru/Big.md", "content": content}, written(path="nuru/big.md")))])
    events = h.finished(h.submit(h.conversation())[1]["turn_id"])
    (start,) = [e for e in events if e["type"] == "tool_start"]
    assert start["data"]["arguments"]["content"] == "x" * protocol.MAX_ARGUMENT_CHARS + "…(cut: 400000 characters)"
    assert start["data"]["arguments"]["note"] == "nuru/Big.md" and events[-1]["type"] == "completed"


def test_shown_arguments_keep_short_values_and_cut_the_rest():
    many = {f"k{i}": i for i in range(40)}
    assert protocol.shown_arguments(many) == {f"k{i}": i for i in range(32)}
    nested = {"rows": [[1, 2]] * 2000, "query": "trip"}
    shown_args = protocol.shown_arguments(nested)
    assert shown_args["query"] == "trip" and shown_args["rows"].endswith("characters)")


def test_an_outcome_that_cant_be_stored_stops_the_turn_and_is_never_taken_by_another_call(harness, monkeypatch):
    """The reviewer's case: the first write's receipt fails to store once; then, with tools closed, an
    unshown refusal of a second write comes. Only the first write ran, and only it gets a receipt."""
    dispatched = []

    def script(session, text, cancel):
        first = shown_call("write_note", {"note": "nuru/first.md"})
        yield first
        dispatched.append("nuru/first.md")
        report(session, first, written(path="nuru/first.md"))
        assert cancel.is_set()  # the turn was told to stop
        # Session's refusal of a call it never showed (TOOLS_CLOSED) has no call ID: on_tool_outcome isn't called.
        yield Text("stopping")

    h = harness([script])
    store, real_add = h.service.store, h.service.store.add_event
    failed = []

    def add_event(event):
        if event["type"] == "write_receipt" and not failed:
            failed.append(event)
            raise sqlite3.OperationalError("disk I/O error")
        real_add(event)

    monkeypatch.setattr(store, "add_event", add_event)
    events = h.finished(h.submit(h.conversation())[1]["turn_id"])
    receipts = [e["data"] for e in events if e["type"] == "write_receipt"]
    assert [(r["state"], r["path"]) for r in receipts] == [("applied", "nuru/first.md")]  # recorded as the turn ended
    assert events[-1]["data"] == {"code": "internal", "message": host.UNRECORDED}
    assert dispatched == ["nuru/first.md"]  # the write ran once


CRASHING_HOST_WRITES = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from core.conversation_store import ConversationStore
from core.service.host import Service
from core.service.store import ServiceStore
from core import questions
from core.tool_dispatch import failure
from test_service_host import ScriptedSession, report, shown_call

root, case = Path(sys.argv[1]), sys.argv[3]
vault = str((root / "vault").resolve())
conversations = ConversationStore(root / "conversation.db")
calls = []

def write(state, target=None, exit_after=True):
    def script(session, text, cancel):
        call = shown_call("write_note", {"note": "Trip plan"})
        calls.append(call.call_id)
        yield call
        attempt = conversations.begin_write(session.conversation_id, "logger-turn", vault, "write_note", {}, call_id=call.call_id)
        if state != "in_flight":
            conversations.finish_write(attempt, state, target=target)  # committed; the process dies before the hook
        if exit_after:
            print(json.dumps(calls), flush=True)
            os._exit(9)
        report(session, call, failure("transport", "TimeoutError", "no answer", certainty="uncertain"))
    return script

def asks_then_dies(session, text, cancel):
    yield shown_call("write_note", {"note": "shared/Team.md"})
    session.answer_question(questions.shared_write("write_note", {}, "shared/Team.md"))

scripts = {
    "in_flight": [write("in_flight")],
    "completed": [write("completed", "nuru/trip-plan.md")],
    "not_completed": [write("not_completed")],
    # A records an uncertain write and ends; B, queued behind it, is cut short at its approval, before any write.
    "queued": [write("uncertain", exit_after=False), asks_then_dies],
}[case]
session = ScriptedSession(scripts)
service = Service(ServiceStore(root / "service.db"), conversations, lambda conversation_id: session, root / "vault",
                  answer_seconds=3600)
conversation = service.create_conversation("operator", {"request_id": "req-conversation-0001"})[1]["conversation_id"]
turns = [service.submit_turn("operator", conversation, {"request_id": f"req-turn-0000000{i}", "text": "go"})[1]["turn_id"]
         for i in range(1, len(scripts) + 1)]
if case == "queued":
    seen = []
    while not any(e["type"] == "question" for e in seen):
        seen += service.wait_events(turns[1], len(seen), 5)[0]
    print(json.dumps(calls), flush=True)
    os._exit(9)
import time
time.sleep(60)
"""


def crash(tmp_path, case):
    (tmp_path / "vault").mkdir(exist_ok=True)
    crashed = subprocess.run([sys.executable, "-c", CRASHING_HOST_WRITES, str(tmp_path), str(Path(__file__).parent), case],
                             capture_output=True, text=True, timeout=60)
    assert crashed.returncode == 9, crashed.stderr
    restarted = Service(ServiceStore(tmp_path / "service.db"), ConversationStore(tmp_path / "conversation.db"),
                        lambda conversation_id: ScriptedSession([]), tmp_path / "vault")
    return restarted, json.loads(crashed.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("case,state,path", [("in_flight", "uncertain", None), ("completed", "applied", "nuru/trip-plan.md"),
                                             ("not_completed", "failed", None)])
def test_after_a_crash_each_write_gets_the_receipt_its_own_ledger_entry_shows(tmp_path, case, state, path):
    restarted, (call_id,) = crash(tmp_path, case)
    try:
        turn = restarted.request_state(ME, "req-turn-00000001", "submit_turn")["turn_id"]
        events = restarted.store.events(turn)
        protocol.check_stream(events)
        (receipt,) = [e["data"] for e in events if e["type"] == "write_receipt"]
        assert (receipt["call_id"], receipt["state"], receipt.get("path")) == (call_id, state, path)
        assert next(e["data"]["ok"] for e in events if e["type"] == "tool_result") is (state == "applied")
        assert events[-1]["data"]["code"] == "service_restarted"
        still_blocking = restarted.conversations.pending_writes(str((tmp_path / "vault").resolve()))
        assert len(still_blocking) == (state == "uncertain")
    finally:
        restarted.close()
        restarted.store.close()


def test_after_a_crash_a_queued_turn_never_gets_an_earlier_turn_s_write(tmp_path):
    restarted, _ = crash(tmp_path, "queued")
    try:
        first, second = (restarted.request_state(ME, f"req-turn-0000000{i}", "submit_turn")["turn_id"] for i in (1, 2))
        assert [e["data"]["state"] for e in restarted.store.events(first) if e["type"] == "write_receipt"] == ["uncertain"]
        events = restarted.store.events(second)
        protocol.check_stream(events)
        assert not any(e["type"] in ("write_receipt", "tool_result") for e in events)  # it never wrote
        closed = next(e["data"] for e in events if e["type"] == "question_closed")
        assert (closed["outcome"], closed["decision"]) == ("expired", "No")
    finally:
        restarted.close()
        restarted.store.close()


def uncertain_write_then_failure(h):
    """As Session handles an uncertain write: report it, then end the turn with a Failure. The attempt is
    in the write ledger under the call's ID, as Session records it."""
    def script(session, text, cancel):
        call = shown_call("write_note", {"note": "nuru/log.md"})
        yield call
        h.service.conversations.begin_write(session.conversation_id, "logger-turn", str(h.vault.resolve()),
                                            "write_note", {}, call_id=call.call_id)
        report(session, call, failure("transport", "TimeoutError", "no answer", certainty="uncertain"))
        yield Failure("The outcome of write_note is uncertain. Do not retry the write.")
    return script


def failing_receipts(monkeypatch, store, times):
    real_add, failed = store.add_event, []

    def add_event(event):
        if event["type"] == "write_receipt" and len(failed) < times:
            failed.append(event)
            raise sqlite3.OperationalError("disk I/O error")
        real_add(event)

    monkeypatch.setattr(store, "add_event", add_event)


def test_a_turn_never_ends_before_a_receipt_that_failed_to_store(harness, monkeypatch):
    """The receipt of an uncertain write fails to store once; Session's Failure that follows must not end
    the turn first."""
    h = harness([])
    h.session.scripts.append(uncertain_write_then_failure(h))
    failing_receipts(monkeypatch, h.service.store, times=1)
    events = h.finished(h.submit(h.conversation())[1]["turn_id"])
    kinds = [e["type"] for e in events]
    assert kinds.index("write_receipt") < kinds.index("failed") == len(kinds) - 1
    assert next(e["data"]["state"] for e in events if e["type"] == "write_receipt") == "uncertain"


def test_while_a_receipt_still_cant_be_stored_the_turn_stays_open_and_a_restart_recovers_it(harness, monkeypatch):
    h = harness([])
    h.session.scripts.append(uncertain_write_then_failure(h))
    failing_receipts(monkeypatch, h.service.store, times=10**6)
    turn = h.submit(h.conversation())[1]["turn_id"]
    h.first(turn, "tool_start")
    assert h.service.close(timeout=5)  # the worker is done with the turn, which never got an end
    events = h.service.store.events(turn)
    assert not any(e["type"] in protocol.TERMINAL for e in events) and not any(e["type"] == "write_receipt" for e in events)
    monkeypatch.undo()
    h.service.store.close()
    h.service.conversations.close()
    restarted = h.service = h.open()
    events = restarted.store.events(turn)
    protocol.check_stream(events)
    assert [e["data"]["state"] for e in events if e["type"] == "write_receipt"] == ["uncertain"]
    assert events[-1]["data"]["code"] == "service_restarted"


# ---------------------------------------------------------------------- conversation titles (TODO F25)
def titled(tmp_path, scripts, titles):
    return Harness(tmp_path, scripts, titles=titles)


def test_a_new_conversations_first_turn_names_it_before_the_turn_is_reported_finished(tmp_path):
    asked = []
    def titles(message, reply):
        asked.append((message, reply))
        return "Lisbon trip plan"
    h = titled(tmp_path, [reply("Flights are ", "about 400 EUR."), reply("Second.")], titles)
    try:
        conversation_id = h.conversation()
        _, body = h.submit(conversation_id, "plan my Lisbon trip")
        events = h.finished(body["turn_id"])
        assert events[-1]["type"] == "completed"
        assert h.service.store.title(conversation_id) == "Lisbon trip plan", "named before 'completed' was stored"
        assert asked == [("plan my Lisbon trip", "Flights are about 400 EUR.")]
        _, body = h.submit(conversation_id, "and hotels?", request_id="req-turn-00000002")
        h.finished(body["turn_id"])
        assert len(asked) == 1, "only the first turn names it"
    finally:
        h.close()


def test_a_title_the_client_chose_itself_is_kept_and_the_messages_start_is_replaced(tmp_path):
    h = titled(tmp_path, [reply("ok"), reply("ok")], lambda message, reply: "Named by the model")
    try:
        status, body = h.service.create_conversation(ME, {"request_id": "req-conversation-0002", "title": "My own name"})
        own = body["conversation_id"]
        _, body = h.submit(own, "hello there")
        h.finished(body["turn_id"])
        assert h.service.store.title(own) == "My own name"

        status, body = h.service.create_conversation(ME, {"request_id": "req-conversation-0003", "title": "hello the"})
        prefixed = body["conversation_id"]  # as the plugin names it: the message's first characters
        _, body = h.submit(prefixed, "hello there, plan a trip", request_id="req-turn-00000002")
        h.finished(body["turn_id"])
        assert h.service.store.title(prefixed) == "Named by the model"
    finally:
        h.close()


def test_a_title_that_fails_or_comes_back_empty_never_fails_the_turn(tmp_path, capsys):
    def broken(message, reply):
        raise RuntimeError("Ollama went away")
    for i, titles in enumerate((broken, lambda message, reply: None)):
        (tmp_path / str(i)).mkdir()
        h = titled(tmp_path / str(i), [reply("fine")], titles)
        try:
            conversation_id = h.conversation()
            _, body = h.submit(conversation_id, "hi")
            assert h.finished(body["turn_id"])[-1]["type"] == "completed"
            assert h.service.store.title(conversation_id) is None
        finally:
            h.close()
    assert "couldn't name a conversation" in capsys.readouterr().err


def test_a_cancelled_first_turn_doesnt_name_the_conversation(tmp_path):
    gate, asked = threading.Event(), []
    h = titled(tmp_path, [held(gate, "partial")], lambda message, reply: asked.append(1) or "Never")
    try:
        conversation_id = h.conversation()
        _, body = h.submit(conversation_id, "long one")
        h.first(body["turn_id"], "text")
        h.service.cancel_turn(ME, body["turn_id"], {"request_id": "req-cancel-000001"})
        gate.set()
        assert h.finished(body["turn_id"])[-1]["type"] == "cancelled"
        assert asked == [] and h.service.store.title(conversation_id) is None
    finally:
        h.close()


def test_retitle_changes_a_title_only_if_it_is_still_the_one_read(tmp_path):
    store = ServiceStore(tmp_path / "service.db")
    try:
        store.add_conversation("conv-a", ME, 1, None)
        assert store.retitle("conv-a", None, "First") is True
        assert store.retitle("conv-a", None, "Stale") is False, "someone named it meanwhile"
        assert store.title("conv-a") == "First"
        assert store.retitle("missing", None, "x") is False and store.title("missing") is None
    finally:
        store.close()
