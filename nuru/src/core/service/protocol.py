"""nuru-serve's wire protocol, version 1: what clients send, what the service streams back, and the
rules a JSON Schema can't state. protocol-v1.schema.json holds the shapes; protocol-v1.md explains
them for client authors.

A turn's events are numbered from 1 with no gaps. The first is `accepted`, the last is the one terminal
event (completed, failed or cancelled), and nothing follows it. A client keeps the highest number it
has applied and drops any event at or below it, so a replay after reconnecting never shows anything
twice (see Display).

Kept free of HTTP and Qt, so the service, the CLI and tests can all use it.
"""

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

import jsonschema

from core import questions
from core.questions import Question

VERSION = 1
DEFAULT_PORT = 27125  # RemoteNotes' receiver has 27124
MAX_REQUEST_BYTES = 256 * 1024
# The whole SSE frame (wire()), not just the JSON. Every text field fits within it even at 4 bytes a
# character; it bounds what the schema can't, like tool arguments.
MAX_EVENT_BYTES = 256 * 1024
MAX_TEXT_CHARS = 16000  # one text event (schema)
MAX_REPLY_CHARS = 60000  # completed.reply and cancelled.partial_reply (schema)
# A tool's arguments are shown, not replayed: each value is cut to this many characters (as JSON, when
# it isn't a string), and only the first MAX_ARGUMENTS are kept, so a tool_start always fits its frame.
MAX_ARGUMENT_CHARS = 2000
MAX_ARGUMENTS = 32  # tool_start.arguments maxProperties (schema)
TERMINAL = frozenset({"completed", "failed", "cancelled"})
# What may come before `started`: a turn can be cancelled or fail while it waits in the queue.
BEFORE_START = frozenset({"accepted", "queued", "failed", "cancelled"})
# HTTP status for each error code.
ERRORS = {
    "invalid_request": 400, "unsupported_version": 400, "unauthorized": 401, "forbidden_origin": 403,
    "forbidden": 403, "not_found": 404, "conflict": 409, "question_closed": 409, "wrong_operation": 409,
    "cursor_expired": 410, "payload_too_large": 413, "queue_full": 429, "internal": 500,
    "model_unavailable": 503, "shutting_down": 503,
}
OPERATIONS = ("create_conversation", "submit_turn", "answer_question", "cancel_turn")

SCHEMA_PATH = Path(__file__).with_name("protocol-v1.schema.json")


class ProtocolError(ValueError):
    """Something broke the protocol. `code` is the error code to answer with."""

    def __init__(self, code: str, message: str, **extra) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra  # more for the error body: retry_after_seconds, resync


@cache
def _validator(kind: str) -> jsonschema.Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text())
    return jsonschema.Draft202012Validator({**schema, "$ref": f"#/$defs/{kind}"})


def validate(kind: str, value) -> None:
    """Raise ProtocolError if `value` isn't a valid `kind` (a $defs name: an operation, event,
    status, error...). The message names the first problem, worded for a client author."""
    error = jsonschema.exceptions.best_match(_validator(kind).iter_errors(value))
    if error is not None:
        raise ProtocolError("invalid_request", _problem(error)[:500])


_LIMITS = {"maxLength", "minLength", "maxItems", "minItems", "maximum", "minimum", "maxProperties"}


def _problem(error: jsonschema.ValidationError) -> str:
    """What's wrong, from the field and the rule it broke, never the rejected value: jsonschema's own
    message quotes it, and it may be note content. Field names come from the schema, except a free
    object's keys, and no request has one."""
    where = "/".join(str(p) for p in error.absolute_path) or "the body"
    rule, limit = error.validator, error.validator_value
    if rule == "required":
        given = error.instance if isinstance(error.instance, dict) else {}
        return f"{where}: missing {', '.join(k for k in limit if k not in given)}"
    if rule == "additionalProperties":
        return f"{where}: has a field the schema doesn't allow"
    if rule in _LIMITS:
        return f"{where}: {rule} is {limit}"
    if rule == "type":
        return f"{where}: should be {limit if isinstance(limit, str) else ' or '.join(limit)}"
    if rule in ("enum", "const"):
        return f"{where}: not an allowed value"
    if rule in ("pattern", "not"):
        return f"{where}: not in the required format"
    return f"{where}: fails {rule}"


def parse_request(operation: str, body: bytes):
    """A client's request body, checked. Size first, so an oversized body is never parsed."""
    if len(body) > MAX_REQUEST_BYTES:
        raise ProtocolError("payload_too_large", f"requests are at most {MAX_REQUEST_BYTES} bytes")
    try:
        value = json.loads(body)
    except ValueError:
        raise ProtocolError("invalid_request", "the body isn't JSON") from None
    validate(operation, value)
    return value


def error_body(code: str, message: str, **extra) -> dict:
    return {"v": VERSION, "error": {"code": code, "message": message[:500], **extra}}


def new_id() -> str:
    return secrets.token_urlsafe(12)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def request_key(principal: str, vault: str, operation: str, request_id: str) -> str:
    """What a request ID is scoped to: the same ID from another principal, vault or operation is a
    different request."""
    return hashlib.sha256(canonical([principal, vault, operation, request_id])).hexdigest()


def payload_hash(target: str, payload: dict) -> str:
    """What a request ID is bound to: the resource the URL names (the conversation a turn goes to, the
    turn to cancel, the question to answer; "" for a new conversation) and the body. A retry with the
    same hash gets the result already given; a different hash under the same ID, such as the same body
    sent to another conversation, is a conflict."""
    body = {k: v for k, v in payload.items() if k != "request_id"}
    return hashlib.sha256(canonical({"target": target, "body": body})).hexdigest()


def operation_hash(turn_id: str, question_id: str, question: dict) -> str:
    """Ties an answer to the one operation it was asked about."""
    return hashlib.sha256(canonical([turn_id, question_id, question])).hexdigest()


def vault_id(vault: Path) -> str:
    """The vault's identity in status: a hash of its resolved path, never the path. A client computes
    the same for its own vault and refuses to chat if the two differ."""
    return hashlib.sha256(str(Path(vault).resolve()).encode()).hexdigest()[:32]


def shown_arguments(arguments: dict) -> dict:
    """A tool's arguments as a client is shown them: long values cut, with a note saying how long they
    were, and at most MAX_ARGUMENTS of them. A write_note's whole content would otherwise make a frame
    over MAX_EVENT_BYTES, and the turn would fail for want of a display."""
    shown = {}
    for key in list(arguments)[:MAX_ARGUMENTS]:
        value = arguments[key]
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if len(text) > MAX_ARGUMENT_CHARS:
            value = f"{text[:MAX_ARGUMENT_CHARS]}…(cut: {len(text)} characters)"
        shown[str(key)[:200]] = value
    return shown


def question_data(question: Question) -> dict:
    data = {"kind": question.kind, "text": question.text,
            "options": [{"label": o.label, **({"description": o.description} if o.description else {}),
                         **({"recommended": True} if o.recommended else {})} for o in question.choices]}
    if question.details:
        data["details"] = question.details
    if question.allow_other:
        data["allow_other"] = True
    return data


# --------------------------------------------------------------------------- streams
def check_event(event: dict) -> None:
    validate("event", event)
    if len(sse(event)) > MAX_EVENT_BYTES:
        raise ProtocolError("payload_too_large", f"event {event['seq']} is over {MAX_EVENT_BYTES} bytes on the wire")


class StreamCheck:
    """Checks one turn's events as they come, in order, from its first: the rules the schema can't
    state. feed() raises ProtocolError at the first event that breaks them, and that event doesn't
    count: the next one is checked as if it had never been sent."""

    def __init__(self) -> None:
        self.turn: tuple[str, str] | None = None
        self.count, self.started, self.ended = 0, False, False
        self.open_questions: dict[str, dict] = {}  # question_id -> the question
        self.closed_questions: set[str] = set()
        self.calls: set[str] = set()

    def feed(self, event: dict) -> None:
        check_event(event)
        number, kind, data = self.count + 1, event["type"], event["data"]

        def broken(why: str):
            raise ProtocolError("invalid_request", f"event {number} ({kind}): {why}")

        turn = (event["conversation_id"], event["turn_id"])
        if self.turn is not None and turn != self.turn:
            broken("belongs to another turn")
        if event["seq"] != number:
            broken(f"seq is {event['seq']}, expected {number}")
        if self.ended:
            broken("comes after the terminal event")
        if (number == 1) != (kind == "accepted"):
            broken("a stream starts with accepted, once")
        if kind == "started":
            if self.started:
                broken("started twice")
            self.started = True
        elif not self.started and kind not in BEFORE_START:
            broken("comes before started")
        if kind == "tool_start":
            if data["call_id"] in self.calls:
                broken("reuses a call_id")
            self.calls.add(data["call_id"])
        elif kind in ("tool_result", "write_receipt") and data["call_id"] not in self.calls:
            broken("names a call that never started")
        elif kind == "question":
            if data["question_id"] in self.open_questions.keys() | self.closed_questions:
                broken("reuses a question_id")
            if data["operation"] != operation_hash(event["turn_id"], data["question_id"], data["question"]):
                broken("operation doesn't match the question")
            self.open_questions[data["question_id"]] = data["question"]
        elif kind == "question_closed":
            question = self.open_questions.get(data["question_id"])
            if question is None:
                broken("closes a question that isn't open")
            answered = data["outcome"] == "answered"
            if answered != ("chosen" in data) or (not answered and "other" in data):
                broken("only an answered question has chosen labels or other text")
            if answered and not answer_is_valid(question, data["chosen"], data.get("other")):
                broken("the answer isn't one the question allows")
            chosen = data["chosen"] if answered else None
            if data["decision"] != decision(chosen, data.get("other")):
                broken("decision doesn't match the answer")
            del self.open_questions[data["question_id"]]
            self.closed_questions.add(data["question_id"])
        elif kind in TERMINAL:
            if self.open_questions:
                broken("ends the turn with a question still open")
            self.ended = True
        self.count, self.turn = number, turn  # only an event that passed counts


def check_stream(events: list[dict], *, finished: bool = True) -> None:
    """Raise ProtocolError unless `events` is one turn's stream, in order, from its first event. With
    finished=False the stream may still be going (no terminal event yet)."""
    if not events:
        raise ProtocolError("invalid_request", "a stream starts with accepted")
    check = StreamCheck()
    for event in events:
        check.feed(event)
    if finished and not check.ended:
        raise ProtocolError("invalid_request", "the stream has no terminal event")


def after(events: list[dict], cursor: int) -> list[dict]:
    """The events a client that has applied up to `cursor` still needs. A cursor beyond the stream
    can't be honoured: ProtocolError("cursor_expired") tells the client to rebuild, never to resubmit."""
    if cursor < 0 or cursor > (events[-1]["seq"] if events else 0):
        raise ProtocolError("cursor_expired", f"no event {cursor} in this turn")
    return [e for e in events if e["seq"] > cursor]


def sse(event: dict) -> bytes:
    """One event as a server-sent-events frame: what goes on the wire, and what MAX_EVENT_BYTES
    measures. The id is the seq, so a browser EventSource resumes with Last-Event-ID; the JSON is
    compact and on one line."""
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {data}\n\n".encode()


KEEPALIVE = b": keepalive\n\n"


def parse_sse(stream: bytes) -> list[dict]:
    """The events in a run of SSE frames (comments skipped). For tests and Python clients."""
    events = []
    for frame in stream.decode().split("\n\n"):
        data = [line[len("data: "):] for line in frame.split("\n") if line.startswith("data: ")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


# --------------------------------------------------------------------------- what a client shows
@dataclass
class Display:
    """What a client shows for one turn, built by applying its events. Applying an event twice, or
    replaying from any earlier cursor, changes nothing: events at or below `seq` are dropped."""

    seq: int = 0
    reply: str = ""
    reasoning: str = ""
    tools: list[dict] = field(default_factory=list)
    receipts: list[dict] = field(default_factory=list)
    question: dict | None = None  # the open one, waiting for an answer
    decisions: list[dict] = field(default_factory=list)
    state: str = "accepted"  # accepted, queued, running, or the terminal type
    ask: dict | None = None
    truncated: bool = False  # the reply was longer than the protocol carries
    message: str = ""

    def apply(self, event: dict) -> None:
        if event["seq"] <= self.seq:
            return
        self.seq = event["seq"]
        kind, data = event["type"], event["data"]
        if kind == "queued":
            self.state = "queued"
        elif kind == "started":
            self.state = "running"
        elif kind == "text":
            self.reply += data["text"]
        elif kind == "reasoning":
            self.reasoning = data["text"] if data["final"] else self.reasoning + data["text"]
        elif kind == "tool_start":
            self.tools.append({"call_id": data["call_id"], "name": data["name"], "ok": None})
        elif kind == "tool_result":
            for tool in self.tools:
                if tool["call_id"] == data["call_id"]:
                    tool["ok"] = data["ok"]
        elif kind == "write_receipt":
            self.receipts.append(data)
        elif kind == "question":
            self.question = data
        elif kind == "question_closed":
            self.question = None
            self.decisions.append(data)
        elif kind in TERMINAL:
            self.state = kind
            self.truncated = data.get("truncated", False)
            if kind == "completed":
                self.reply, self.ask = data["reply"], data.get("ask")
            elif kind == "failed":
                self.message = data["message"]
            else:
                self.reply = data.get("partial_reply", self.reply)


# --------------------------------------------------------------------------- building a stream
class TurnStream:
    """Numbers one turn's events and checks each as it is made, so the service can't send a stream
    clients would reject."""

    def __init__(self, conversation_id: str, turn_id: str) -> None:
        self.conversation_id, self.turn_id = conversation_id, turn_id
        self.events: list[dict] = []
        self.reply = ""  # what the text events carried, at most MAX_REPLY_CHARS
        self.truncated = False  # text beyond that was dropped
        self._check = StreamCheck()

    @classmethod
    def resume(cls, conversation_id: str, turn_id: str, events: list[dict]) -> "TurnStream":
        """The stream of a turn whose events so far are `events` (stored before a restart), ready to
        go on from them."""
        turn = cls(conversation_id, turn_id)
        for event in events:
            turn._add(event)
        turn.truncated = len(turn.reply) >= MAX_REPLY_CHARS
        return turn

    @property
    def ended(self) -> bool:
        return self._check.ended

    @property
    def open_questions(self) -> list[str]:
        return list(self._check.open_questions)

    def emit(self, kind: str, data: dict) -> dict:
        event = {"v": VERSION, "conversation_id": self.conversation_id, "turn_id": self.turn_id,
                 "seq": len(self.events) + 1, "at": now(), "type": kind, "data": data}
        return self._add(event)

    def _add(self, event: dict) -> dict:
        self._check.feed(event)
        self.events.append(event)
        if event["type"] == "text":
            self.reply += event["data"]["text"]
        return event

    def text(self, text: str) -> list[dict]:
        """`text` as text events of at most MAX_TEXT_CHARS each (a buffered reply comes as one long
        piece). Past MAX_REPLY_CHARS in all, the rest is dropped and the turn ends truncated."""
        room = MAX_REPLY_CHARS - len(self.reply)
        if len(text) > room:
            text, self.truncated = text[:room], True
        return [self.emit("text", {"text": text[i:i + MAX_TEXT_CHARS]}) for i in range(0, len(text), MAX_TEXT_CHARS)]

    def completed(self, ask: dict | None = None) -> dict:
        return self.emit("completed", {"reply": self.reply, **({"ask": ask} if ask else {}),
                                       **({"truncated": True} if self.truncated else {})})

    def cancelled(self, reason: str) -> dict:
        return self.emit("cancelled", {"reason": reason, "partial_reply": self.reply,
                                       **({"truncated": True} if self.truncated else {})})

    def question(self, question: Question, expires_at: str) -> dict:
        question_id, data = new_id(), question_data(question)
        return self.emit("question", {"question_id": question_id, "expires_at": expires_at, "question": data,
                                      "operation": operation_hash(self.turn_id, question_id, data)})

    def session_event(self, event) -> list[dict]:
        """A Session event as protocol events. A tool's result and write receipt don't come from here:
        Session reports them through its on_tool_outcome hook (the service's Service._tool_outcome)."""
        from core import session  # heavy (MCP, Ollama, Laya); the protocol alone doesn't need it

        if isinstance(event, session.Text):
            return self.text(event.text)
        if isinstance(event, session.ThinkingChunk):
            return [self.emit("reasoning", {"text": event.text, "final": False})]
        if isinstance(event, session.Thinking):
            return [self.emit("reasoning", {"text": event.text, "final": True})]
        if isinstance(event, session.Steered):
            return [self.emit("retry", {"reason": event.reason or "steered"})]
        if isinstance(event, session.RetryWithoutThinking):
            return [self.emit("retry", {"reason": "silent"})]
        if isinstance(event, session.ToolCall):  # the call's own ID, so its outcome finds it (on_tool_outcome)
            return [self.emit("tool_start", {"call_id": event.call_id or new_id(), "name": event.name,
                                             "arguments": shown_arguments(event.arguments)})]
        if isinstance(event, session.Failure):
            return [self.emit("failed", {"code": "turn_failed", "message": event.message})]
        if isinstance(event, session.Ask):
            return [self.completed(question_data(event.question))]
        raise TypeError(f"no protocol event for {type(event).__name__}")


def answer_is_valid(question: dict, chosen: list[str], other: str | None = None) -> bool:
    """Whether `chosen` (and `other`, the user's own words) answer `question` (a question event's
    `question`): its own labels, each once. yes_no and single take exactly one label or, where
    allow_other is set, the user's own words instead. multi takes any of its labels, none included,
    and the user's own words besides where allowed."""
    if other is not None and not (other.strip() and question.get("allow_other")):
        return False
    labels = {o["label"] for o in question["options"]}
    if not set(chosen) <= labels or len(set(chosen)) != len(chosen):
        return False
    return question["kind"] == "multi" or len(chosen) + (other is not None) == 1


def decision(chosen: list[str] | None, other: str | None = None) -> str:
    """The decision a closed question records. No answer (chosen None) is No; an answer that picked
    nothing is "None of these.", as the model would be told."""
    if chosen is None:
        return questions.NO
    return questions.answer_text([*chosen, *([other] if other is not None else [])])
