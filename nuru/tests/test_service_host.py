"""nuru-serve's host (core.service.host) with a scripted Session: no model, no MCP, no HTTP. Turns that
must wait are held by threading.Events the test releases, never by sleeps."""

import json
import os
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

ME, SOMEONE_ELSE = "operator", "intruder"
WAIT = 10  # seconds before a test gives up on an event it expects


class ScriptedSession:
    """Stands in for Session. Each ask() runs the next script: a function (session, text, cancel)
    yielding Session events. Records which conversation each turn ran in."""

    def __init__(self, scripts):
        self.scripts = scripts
        self.conversation_id = None
        self.answer_question = None
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
        return next(e for e in self.events_until(turn_id, lambda es: any(e["type"] == kind for e in es)) if e["type"] == kind)

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


def test_a_model_that_cant_start_fails_the_turn_and_is_retried_next_time(harness):
    h = harness([reply("back")], factory_error=OSError("no ollama"))
    conversation = h.conversation()
    events = h.finished(h.submit(conversation, request_id="req-turn-00000001")[1]["turn_id"])
    assert events[-1]["data"]["code"] == "model_unavailable"
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
        assert db.execute("SELECT version FROM service_migrations").fetchall() == [(1,)]
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


def test_a_change_that_cant_be_rolled_back_stops_the_service_taking_work(harness, monkeypatch):
    gate = threading.Event()
    h = harness([held(gate, "first")])
    conversation = h.conversation()
    first = h.submit(conversation, request_id="req-turn-00000001")[1]["turn_id"]
    h.first(first, "text")
    store = h.service.store
    real = store.transaction
    monkeypatch.setattr(store, "transaction", lambda: StuckTransaction(store._conn))
    with pytest.raises(sqlite3.OperationalError):
        h.submit(conversation, request_id="req-turn-00000002")
    monkeypatch.setattr(store, "transaction", real)
    store._conn.execute("ROLLBACK")  # what a restart would find: the change never committed
    for retry in ("req-turn-00000002", "req-turn-00000001"):  # neither new work nor a replay is trusted
        with pytest.raises(ProtocolError) as refused:
            h.submit(conversation, request_id=retry)
        assert refused.value.code == "internal"
    gate.set()
