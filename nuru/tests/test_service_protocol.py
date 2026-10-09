"""Protocol v1 (WP01 M1): the frozen fixtures hold, and the rules clients rely on are enforced."""

import copy
import json
import typing
from pathlib import Path

import pytest

from core import questions, session
from core.service import protocol
from core.service.protocol import Display, ProtocolError, TurnStream

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "protocol_v1.json").read_text())
STREAMS = FIXTURES["streams"]


def shown(events):
    display = Display()
    for event in events:
        display.apply(event)
    return display


@pytest.mark.parametrize("name", STREAMS)
def test_fixture_streams_are_valid_and_end_once(name):
    events = STREAMS[name]
    protocol.check_stream(events)
    assert [e["type"] in protocol.TERMINAL for e in events].count(True) == 1
    assert events[-1]["type"] in protocol.TERMINAL


def test_fixtures_cover_every_event_type_and_terminal_state():
    schema = json.loads(protocol.SCHEMA_PATH.read_text())
    types = set(schema["$defs"]["event"]["properties"]["type"]["enum"])
    assert {e["type"] for events in STREAMS.values() for e in events} == types
    outcomes = {e["data"]["outcome"] for events in STREAMS.values() for e in events if e["type"] == "question_closed"}
    assert outcomes == {"answered", "expired", "cancelled"}


def test_display_of_each_fixture():
    assert shown(STREAMS["completed_with_tools"]).reply.startswith("Found **3** notes: [[Grüße]], [[🌍 Trip]]")
    assert shown(STREAMS["completed_with_tools"]).reasoning == "The user wants the meeting notes."
    assert shown(STREAMS["completed_with_tools"]).tools == [{"call_id": "call00search", "name": "search", "ok": True}]
    approved = shown(STREAMS["write_approved"])
    assert ([r["state"] for r in approved.receipts], approved.question) == (["applied"], None)
    expired = shown(STREAMS["write_expired"])
    assert (expired.decisions[0]["decision"], expired.receipts[0]["state"]) == ("No", "refused")
    assert shown(STREAMS["cancelled_while_asking"]).reply == "I'll save that. "
    assert shown(STREAMS["cancelled_in_queue"]).state == "cancelled"
    assert shown(STREAMS["model_asks"]).ask["kind"] == "single"
    assert shown(STREAMS["failed"]).message.startswith("The model stopped")
    assert shown(STREAMS["uncertain_write"]).receipts[0]["state"] == "uncertain"


@pytest.mark.parametrize("name", STREAMS)
def test_replay_from_every_cursor_shows_nothing_twice(name):
    events = STREAMS[name]
    whole = shown(events)
    for cursor in range(len(events) + 1):
        display = shown(events[:cursor])
        for event in protocol.after(events, cursor) + events:  # the replay, then everything again
            display.apply(event)
        assert display == whole


def test_a_cursor_past_the_stream_asks_for_a_resync_not_a_new_turn():
    events = STREAMS["failed"]
    for cursor in (-1, len(events) + 1):
        with pytest.raises(ProtocolError) as raised:
            protocol.after(events, cursor)
        assert raised.value.code == "cursor_expired"
    assert protocol.after(events, len(events)) == []


def broken(name, change):
    events = copy.deepcopy(STREAMS[name])
    change(events)
    with pytest.raises(ProtocolError) as raised:
        protocol.check_stream(events)
    return str(raised.value)


def test_streams_that_break_the_rules_are_refused():
    assert "expected 3" in broken("failed", lambda e: e[2].update(seq=4))
    assert "after the terminal" in broken("failed", lambda e: e.append({**e[-1], "seq": len(e) + 1}))
    assert "no terminal" in broken("failed", lambda e: e.pop())
    assert "starts with accepted" in broken("failed", lambda e: e.pop(0) and [x.update(seq=x["seq"] - 1) for x in e])
    assert "another turn" in broken("failed", lambda e: e[2].update(turn_id="turn99999999"))
    assert "before started" in broken("retry", lambda e: e.__setitem__(1, {**e[1], "type": "text", "data": {"text": "x"}}))
    assert "never started" in broken("write_approved", lambda e: e[5]["data"].update(call_id="call0000other"))
    assert "operation" in broken("write_approved", lambda e: e[3]["data"]["question"].update(text="Allow something else?"))
    assert "still open" in broken("write_refused", lambda e: [e.pop(4) for _ in range(3)] and
                                   [x.update(seq=i) for i, x in enumerate(e, 1)])
    assert "chosen" in broken("write_expired", lambda e: e[4]["data"].update(chosen=["No"]))
    assert "type" in broken("failed", lambda e: e[2].update(type="debug"))
    assert "data" in broken("failed", lambda e: e[2]["data"].update(extra=1))


def test_an_oversized_event_is_refused():
    events = copy.deepcopy(STREAMS["completed_with_tools"])
    events[-1]["data"]["reply"] = "🌍" * 60_000  # the longest reply, at 4 bytes a character, still fits
    protocol.check_stream(events)
    events[6]["data"]["arguments"]["query"] = "x" * protocol.MAX_EVENT_BYTES
    with pytest.raises(ProtocolError) as raised:
        protocol.check_stream(events)
    assert raised.value.code == "payload_too_large"


@pytest.mark.parametrize("kind", FIXTURES["requests"])
def test_fixture_requests(kind):
    for value in FIXTURES["requests"][kind]["valid"]:
        protocol.validate(kind, value)
    for why, value in FIXTURES["requests"][kind]["invalid"].items():
        with pytest.raises(ProtocolError, match="."):
            protocol.validate(kind, value)
        assert why


def test_requests_are_size_checked_before_they_are_parsed():
    with pytest.raises(ProtocolError) as raised:
        protocol.parse_request("submit_turn", b"{" * (protocol.MAX_REQUEST_BYTES + 1))
    assert raised.value.code == "payload_too_large"
    with pytest.raises(ProtocolError) as raised:
        protocol.parse_request("submit_turn", b"not json")
    assert raised.value.code == "invalid_request"
    body = json.dumps(FIXTURES["requests"]["submit_turn"]["valid"][0]).encode()
    assert protocol.parse_request("submit_turn", body)["text"] == "What did I plan for Friday?"


def test_error_codes_match_the_schema():
    schema = json.loads(protocol.SCHEMA_PATH.read_text())
    assert set(schema["$defs"]["error"]["properties"]["error"]["properties"]["code"]["enum"]) == set(protocol.ERRORS)
    protocol.validate("error", protocol.error_body("conflict", "request ID reused with other content"))


def test_request_ids_are_scoped_and_bound_to_their_content():
    key = protocol.request_key("plugin", "vault0000demo", "submit_turn", "req-0000000000000001")
    assert key != protocol.request_key("phone", "vault0000demo", "submit_turn", "req-0000000000000001")
    assert key != protocol.request_key("plugin", "vault0000othr", "submit_turn", "req-0000000000000001")
    assert key != protocol.request_key("plugin", "vault0000demo", "cancel_turn", "req-0000000000000001")
    first = {"request_id": "req-0000000000000001", "text": "hi"}
    assert protocol.payload_hash("conv0000demo", first) == protocol.payload_hash("conv0000demo", {**first, "request_id": "req-0000000000000002"})
    assert protocol.payload_hash("conv0000demo", first) != protocol.payload_hash("conv0000demo", {**first, "text": "hi!"})


@pytest.mark.parametrize("case", FIXTURES["request_binding"]["cases"], ids=lambda c: c["why"])
def test_a_reused_request_id_replays_only_for_the_same_target_and_body(case):
    keys, hashes = set(), set()
    for sent in (case["first"], case["second"]):
        protocol.validate(case["operation"], sent["body"])
        keys.add(protocol.request_key("operator", "vault0000demo", case["operation"], sent["body"]["request_id"]))
        hashes.add(protocol.payload_hash(sent["target"], sent["body"]))
    assert len(keys) == 1  # the same request ID, so the second is a replay or a conflict, never new work
    assert ("replay" if len(hashes) == 1 else "conflict") == case["expect"]


def test_sse_frames_round_trip_with_newlines_and_unicode():
    events = STREAMS["completed_with_tools"]
    stream = b"".join(protocol.sse(e) for e in events[:5]) + protocol.KEEPALIVE + b"".join(protocol.sse(e) for e in events[5:])
    assert protocol.parse_sse(stream) == events
    assert protocol.sse(events[-1]).startswith(b"id: 12\nevent: completed\ndata: {")


def test_every_session_event_has_a_protocol_event():
    turn = TurnStream("conv0000demo", "turn00000001")
    turn.emit("accepted", {"request_id": "req-0000000000000001"})
    turn.emit("started", {})
    samples = {
        session.Text: session.Text("Hi "), session.ThinkingChunk: session.ThinkingChunk("hm"),
        session.Thinking: session.Thinking("hmm"), session.RetryWithoutThinking: session.RetryWithoutThinking(),
        session.Steered: session.Steered("repeating"), session.ToolCall: session.ToolCall("search", {"query": "x"}),
    }
    assert set(samples) | {session.Failure, session.Ask} == set(typing.get_args(session.Event))
    for event in samples.values():
        assert turn.session_event(event)
    asked = turn.session_event(session.Ask(questions.yes_no("Go on?", recommend="Yes")))
    assert asked[0]["data"] == {"reply": "Hi ", "ask": {"kind": "yes_no", "text": "Go on?",
                                                         "options": [{"label": "Yes", "recommended": True}, {"label": "No"}]}}
    with pytest.raises(ProtocolError, match="after the terminal"):
        turn.session_event(session.Failure("late"))
    protocol.check_stream(turn.events)

    failing = TurnStream("conv0000demo", "turn00000002")
    failing.emit("accepted", {"request_id": "req-0000000000000002"})
    failing.emit("started", {})
    assert failing.session_event(session.Failure("No model"))[0]["type"] == "failed"


def test_a_raised_question_is_tied_to_its_turn_and_closed_before_the_end():
    turn = TurnStream("conv0000demo", "turn00000001")
    turn.emit("accepted", {"request_id": "req-0000000000000001"})
    turn.emit("started", {})
    asked = turn.question(questions.write("write_note", {"note": "x"}), "2026-10-09T12:00:10.000Z")["data"]
    with pytest.raises(ProtocolError, match="still open"):
        turn.emit("completed", {"reply": ""})
    turn.emit("question_closed", {"question_id": asked["question_id"], "outcome": "expired", "decision": "No"})
    turn.emit("completed", {"reply": ""})  # the refused event wasn't counted
    protocol.check_stream(turn.events)
    assert asked["operation"] != protocol.operation_hash("turn00000002", asked["question_id"], asked["question"])


def test_answers_must_be_ones_the_question_allows():
    yes_no = STREAMS["write_approved"][3]["data"]["question"]
    assert protocol.answer_is_valid(yes_no, ["Yes"]) and protocol.answer_is_valid(yes_no, ["No"])
    assert not protocol.answer_is_valid(yes_no, ["Yes", "No"])
    assert not protocol.answer_is_valid(yes_no, ["Maybe"])
    assert not protocol.answer_is_valid(yes_no, [])
    assert not protocol.answer_is_valid(yes_no, [], other="sure")  # no allow_other
    single = STREAMS["single_answered_other"][2]["data"]["question"]
    assert protocol.answer_is_valid(single, [], other="the other one")
    assert not protocol.answer_is_valid(single, ["nuru/Summary.md"], other="the other one")  # one answer, not two
    assert not protocol.answer_is_valid(single, [], other="  ")
    multi = STREAMS["multi_answered_none"][2]["data"]["question"]
    assert protocol.answer_is_valid(multi, ["user/Rome.md", "user/Oslo.md"]) and protocol.answer_is_valid(multi, [])
    assert not protocol.answer_is_valid(multi, ["user/Rome.md", "user/Rome.md"])


def test_no_answer_is_no_but_an_empty_answer_is_none_of_these():
    assert protocol.decision(None) == "No"
    assert protocol.decision([]) == questions.answer_text([]) == "None of these."
    assert protocol.decision(["a", "b"]) == "a; b"
    assert protocol.decision([], "my own") == "my own"
    assert shown(STREAMS["multi_answered_none"]).decisions[0]["decision"] == "None of these."
    assert shown(STREAMS["single_answered_other"]).decisions[0]["other"] == "nuru/Trips/Summary.md"


def test_a_closed_question_is_checked_against_the_question_asked():
    assert "allows" in broken("write_approved", lambda e: e[4]["data"].update(chosen=["Maybe"], decision="Maybe"))
    assert "allows" in broken("write_approved", lambda e: e[4]["data"].update(other="sure", decision="Yes; sure"))
    assert "allows" in broken("multi_answered_none", lambda e: e[3]["data"].update(chosen=["user/Rome.md"] * 2,
                                                                                   decision="user/Rome.md; user/Rome.md"))
    assert "decision" in broken("write_approved", lambda e: e[4]["data"].update(decision="No"))
    assert "decision" in broken("multi_answered_none", lambda e: e[3]["data"].update(decision="No"))
    assert "only an answered" in broken("write_expired", lambda e: e[4]["data"].update(other="late"))


@pytest.mark.parametrize("chosen,other,decided", [([], None, "None of these."), (None, None, "No"),
                                                  ([], "nuru/Trips/Summary.md", "nuru/Trips/Summary.md")])
def test_a_turn_stream_can_close_every_kind_of_answer(chosen, other, decided):
    turn = TurnStream("conv0000demo", "turn00000001")
    turn.emit("accepted", {"request_id": "req-0000000000000001"})
    turn.emit("started", {})
    question = questions.Question("Which?", questions.MULTI if other is None else questions.SINGLE,
                                  (questions.Option("a"), questions.Option("b")), allow_other=other is not None)
    asked = turn.question(question, "2026-10-09T12:00:10.000Z")["data"]
    closed = {"question_id": asked["question_id"], "decision": protocol.decision(chosen, other)}
    if chosen is None:
        closed["outcome"] = "expired"
    else:
        closed.update(outcome="answered", chosen=chosen, **({"other": other} if other else {}))
    turn.emit("question_closed", closed)
    turn.completed()
    protocol.check_stream(turn.events)
    assert shown(turn.events).decisions[0]["decision"] == decided


def started_turn():
    turn = TurnStream("conv0000demo", "turn00000001")
    turn.emit("accepted", {"request_id": "req-0000000000000001"})
    turn.emit("started", {})
    return turn


def test_a_buffered_reply_is_split_into_text_events_without_changing_it():
    reply = "Grüße 🌍 " * 2001  # 16,008 characters, past one text event's limit
    turn = started_turn()
    sent = turn.session_event(session.Text(reply))
    assert [len(e["data"]["text"]) for e in sent] == [protocol.MAX_TEXT_CHARS, len(reply) - protocol.MAX_TEXT_CHARS]
    turn.completed()
    protocol.check_stream(turn.events)
    assert shown(turn.events).reply == reply and "truncated" not in turn.events[-1]["data"]


def test_a_reply_past_the_limit_ends_truncated_not_unfinished():
    turn = started_turn()
    for _ in range(5):
        turn.session_event(session.Text("🌍" * 15000))
    turn.completed()
    protocol.check_stream(turn.events)
    display = shown(turn.events)
    assert (len(display.reply), display.truncated, display.state) == (protocol.MAX_REPLY_CHARS, True, "completed")

    stopped = started_turn()
    stopped.text("x" * (protocol.MAX_REPLY_CHARS + 1))
    stopped.cancelled("user")
    protocol.check_stream(stopped.events)
    assert shown(stopped.events).truncated and len(shown(stopped.events).reply) == protocol.MAX_REPLY_CHARS


def test_the_event_limit_is_measured_on_the_frame_sent():
    # Nested arguments, where spaces after separators would add a third to the size.
    def tool(n):
        return {"v": 1, "conversation_id": "conv0000demo", "turn_id": "turn00000001", "seq": 3, "type": "tool_start",
                "data": {"call_id": "call00search", "name": "search", "arguments": {"query": [0] * n}}}
    overhead = len(protocol.sse(tool(0)))
    fits = (protocol.MAX_EVENT_BYTES - overhead + 1) // 2  # the first element adds "0", each after it ",0"
    assert len(protocol.sse(tool(fits))) <= protocol.MAX_EVENT_BYTES < len(protocol.sse(tool(fits + 1)))
    protocol.check_event(tool(fits))
    with pytest.raises(ProtocolError) as raised:
        protocol.check_event(tool(fits + 1))
    assert raised.value.code == "payload_too_large"
    assert protocol.parse_sse(protocol.sse(tool(fits))) == [tool(fits)]


SECRET = "CONFIDENTIAL NOTE CONTENT"


@pytest.mark.parametrize("operation,body", [
    ("submit_turn", {"request_id": "req-0000000000000001", "text": SECRET + "x" * 32000}),
    ("submit_turn", {"request_id": "req-0000000000000001", "text": "hi", "attachments": [{"path": "a.md", "selection": SECRET * 1000}]}),
    ("submit_turn", {"request_id": "req-0000000000000001", "text": "hi", "attachments": [{"path": "/" + SECRET}]}),
    ("submit_turn", {"request_id": SECRET, "text": "hi"}),
    ("submit_turn", {"request_id": "req-0000000000000001", "text": "hi", SECRET: 1}),
    ("submit_turn", {"request_id": "req-0000000000000001", "text": [SECRET]}),
    ("answer_question", {"request_id": "req-0000000000000003", "operation": SECRET, "chosen": ["Yes"]}),
    ("answer_question", {"request_id": "req-0000000000000003", "operation": "0" * 64, "chosen": ["Yes"], "other": SECRET * 100}),
])
def test_refusals_never_repeat_what_was_sent(operation, body):
    with pytest.raises(ProtocolError) as raised:
        protocol.parse_request(operation, json.dumps(body).encode())
    message = json.dumps(protocol.error_body(raised.value.code, str(raised.value)))
    assert "CONFIDENTIAL" not in message
    assert str(raised.value).split(":")[0] in ("text", "attachments/0/selection", "attachments/0/path", "request_id",
                                                "the body", "operation", "other")
