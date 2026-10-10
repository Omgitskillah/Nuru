"""nuru-serve's host: the protocol v1 operations, with no HTTP in them (server.py adds that).

One worker thread runs every turn, one at a time, from a bounded FIFO queue. It builds the one shared
Session on the first turn (the model loads then, not at startup) and switches its conversation before
each turn, so nothing conversation-specific may live on the Session between turns. The HTTP threads
only store and read: they never wait on the model, so status, history, Stop and answers stay quick
while a turn runs or waits on a question.

Every turn's events are stored as they are made, before a client sees them. A turn that hadn't ended
when the service stopped ends `failed` (service_restarted) when it starts again: it's never rerun.
"""

import dataclasses
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from importlib import metadata
from pathlib import Path
from typing import Callable

import requests

from core import questions
from core.conversation_store import ConversationStore
from core.questions import Question
from core.service import protocol
from core.service.protocol import Display, ProtocolError, TurnStream
from core.service.store import ServiceStore

QUEUE_LIMIT = 4  # turns waiting behind the running one
RETENTION_DAYS = 30
MAX_PAGE = 50
RESTARTED = "Nuru restarted before this turn finished. Nothing was retried; check any write it made."


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%f%z" if "." in stamp else "%Y-%m-%dT%H:%M:%S%z")


@dataclasses.dataclass
class _Waiter:
    """A question the running turn is waiting on."""

    id: str
    question: dict
    expires: datetime
    done: threading.Event = dataclasses.field(default_factory=threading.Event)
    answer: list[str] | None = None  # None: cancelled


@dataclasses.dataclass
class _Live:
    """A turn that hasn't ended: its stream, and what the worker needs to run it."""

    stream: TurnStream
    store_id: int
    prompt: str
    cancel: threading.Event = dataclasses.field(default_factory=threading.Event)
    cancel_reason: str = "user"
    waiter: _Waiter | None = None


class Service:
    def __init__(self, store: ServiceStore, conversations: ConversationStore, session_factory: Callable[[int], object],
                 vault: Path, *, queue_limit: int = QUEUE_LIMIT, answer_seconds: float | None = None,
                 retention_days: int = RETENTION_DAYS) -> None:
        """`session_factory(conversation_id)` builds the Session (in the worker thread, on the first
        turn). `answer_seconds` overrides how long a question waits (questions.ANSWER_SECONDS)."""
        self.store, self.conversations, self._factory = store, conversations, session_factory
        self.vault = Path(vault).resolve()
        self.vault_id = protocol.vault_id(self.vault)
        self.queue_limit, self.answer_seconds = queue_limit, answer_seconds
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)  # an event was stored, or the queue moved
        self._live: dict[str, _Live] = {}
        self._queue: deque[str] = deque()
        self._active: str | None = None
        self._session = None
        self.chat = "idle"  # idle, loading, ready, unavailable (status)
        self._closing = False
        try:
            self.version = metadata.version("core")
        except metadata.PackageNotFoundError:
            self.version = "dev"
        with self._lock:
            self._recover(retention_days)
        self._worker = threading.Thread(target=self._run, name="nuru-serve-worker", daemon=True)
        self._worker.start()

    # ------------------------------------------------------------------ startup and shutdown
    def _recover(self, retention_days: int) -> None:
        """Compact old turns, and end every turn a stop left unfinished: its questions expire, and it
        fails with service_restarted. A write it may have made stays as the conversation store left it
        (in_flight or uncertain), blocking the next write until the operator resolves it."""
        with self.store.transaction():
            self.store.compact(retention_days)
            for turn in self.store.unfinished_turns():
                live = _Live(TurnStream.resume(turn["conversation_id"], turn["id"], self.store.events(turn["id"])), 0, "")
                for question_id in live.stream.open_questions:
                    self._close_question(live, question_id, "expired")
                self._emit(live, lambda s: s.emit("failed", {"code": "service_restarted", "message": RESTARTED}))

    def close(self, timeout: float = 30) -> None:
        """Stop taking work, cancel the queued turns and the running one, and wait for the worker. The
        Session is closed, never shut down: the Ollama server and its models may be someone else's."""
        with self._lock:
            self._closing = True
            while self._queue:
                live = self._live[self._queue.popleft()]
                self._emit(live, lambda s: s.cancelled("shutdown"))
            if self._active:
                self._cancel_running(self._live[self._active], "shutdown")
            self._changed.notify_all()
        self._worker.join(timeout)

    # ------------------------------------------------------------------ requests
    def _request(self, principal: str, operation: str, payload: dict, target: str, act) -> tuple[int, dict]:
        """Run `act` once per request ID. A retry with the same body and target gets the stored answer;
        the same ID for anything else is a conflict. `act` returns (status, body, conversation ID,
        turn ID, then): it runs in a transaction with the request's record, and `then` runs after the
        commit. If `act` refuses, nothing is recorded, so a retry is judged afresh."""
        key = protocol.request_key(principal, self.vault_id, operation, payload["request_id"])
        digest = protocol.payload_hash(target, payload)
        with self._lock:
            known = self.store.request(key)
            if known:
                if known["payload_hash"] != digest:
                    raise ProtocolError("conflict", "this request_id was already used for a different request")
                body = {**known["body"], "replayed": True} if operation == "submit_turn" else known["body"]
                return known["status"], body
            if self._closing:
                raise ProtocolError("shutting_down", "Nuru is shutting down")
            with self.store.transaction():
                status, body, conversation_id, turn_id, then = act()
                self.store.add_request(key, operation, payload["request_id"], digest, status, body, conversation_id, turn_id)
            if then:
                then()
            self._changed.notify_all()
            return status, body

    def create_conversation(self, principal: str, payload: dict) -> tuple[int, dict]:
        def act():
            # The conversation store first: this side may lag it, never the other way round.
            store_id = self.conversations.create_conversation(payload.get("title"))
            conversation_id = protocol.new_id()
            self.store.add_conversation(conversation_id, principal, store_id, payload.get("title"))
            return 201, {"v": protocol.VERSION, "conversation_id": conversation_id}, conversation_id, None, None

        return self._request(principal, "create_conversation", payload, "", act)

    def submit_turn(self, principal: str, conversation_id: str, payload: dict) -> tuple[int, dict]:
        def act():
            conversation = self.store.conversation(conversation_id, principal)
            if conversation is None:
                raise ProtocolError("not_found", "no such conversation")
            prompt = self._prompt(payload)
            waiting = len(self._queue) + (self._active is not None)
            if waiting > self.queue_limit:
                raise ProtocolError("queue_full", f"{self.queue_limit} turns are already waiting", retry_after_seconds=5)
            turn_id = protocol.new_id()
            self.store.add_turn(turn_id, conversation_id, payload["request_id"], payload["text"])
            live = _Live(TurnStream(conversation_id, turn_id), conversation["store_id"], prompt)
            self._emit(live, lambda s: s.emit("accepted", {"request_id": payload["request_id"]}))
            if waiting:
                self._emit(live, lambda s: s.emit("queued", {"position": waiting}))

            def then():
                self._live[turn_id] = live
                self._queue.append(turn_id)

            body = {"v": protocol.VERSION, "conversation_id": conversation_id, "turn_id": turn_id,
                    "request_id": payload["request_id"], "replayed": False, "events": f"/v1/turns/{turn_id}/events"}
            return 202, body, conversation_id, turn_id, then

        return self._request(principal, "submit_turn", payload, conversation_id, act)

    def _prompt(self, payload: dict) -> str:
        """The message as Session gets it: the text, then what the user attached, if anything.

        TODO(WP01 M3): settle how attachments reach the model with the plugin; for now each is named,
        with its selection quoted."""
        parts = [payload["text"]]
        for attachment in payload.get("attachments", []):
            path = self._vault_relative(attachment["path"])
            parts.append(f"Attached note: {path}")
            if attachment.get("selection"):
                parts.append("Selection:\n" + "\n".join("> " + line for line in attachment["selection"].splitlines()))
        return "\n\n".join(parts)

    def _vault_relative(self, path: str) -> str:
        """`path` if it stays in the vault once resolved, symlinks included; else invalid_request."""
        try:
            return (self.vault / path).resolve().relative_to(self.vault).as_posix()
        except (ValueError, OSError):
            raise ProtocolError("invalid_request", "attachments/path: outside the vault") from None

    def cancel_turn(self, principal: str, turn_id: str, payload: dict) -> tuple[int, dict]:
        def act():
            turn = self.store.turn(turn_id, principal)
            if turn is None:
                raise ProtocolError("not_found", "no such turn")
            live, then = self._live.get(turn_id), None
            if live and not live.stream.ended:
                if turn_id in self._queue:  # not started: it ends here
                    self._emit(live, lambda s: s.cancelled("user"))
                    then = lambda: self._queue.remove(turn_id)  # noqa: E731
                else:
                    then = self._cancel_running(live, "user")
            body = {"v": protocol.VERSION, "conversation_id": turn["conversation_id"], "turn_id": turn_id}
            return 202, body, turn["conversation_id"], turn_id, then

        return self._request(principal, "cancel_turn", payload, turn_id, act)

    def _cancel_running(self, live: _Live, reason: str):
        """Close the question the running turn waits on, if any, and return what stops the turn: the
        worker ends it as cancelled once Session gives up the turn."""
        waiter = live.waiter
        if waiter and not waiter.done.is_set():
            self._close_question(live, waiter.id, "cancelled")

        def then():
            live.cancel_reason = reason
            live.cancel.set()
            if waiter:
                waiter.done.set()

        if self._closing:  # close() has no transaction to wait for
            then()
            return None
        return then

    def answer_question(self, principal: str, question_id: str, payload: dict) -> tuple[int, dict]:
        def act():
            question = self.store.question(question_id, principal)
            if question is None:
                raise ProtocolError("not_found", "no such question")
            live = self._live.get(question["turn_id"])
            waiter = live.waiter if live else None
            # The host's clock decides: past expires_at, the worker is about to close it as expired.
            if not question["open"] or waiter is None or waiter.id != question_id \
                    or datetime.now(timezone.utc) >= waiter.expires:
                raise ProtocolError("question_closed", "this question is closed")
            if payload["operation"] != question["operation"]:
                raise ProtocolError("wrong_operation", "this answer is for another operation")
            chosen, other = payload["chosen"], payload.get("other")
            if not protocol.answer_is_valid(waiter.question, chosen, other):
                raise ProtocolError("invalid_request", "chosen: not an answer this question allows")
            data = self._close_question(live, question_id, "answered", chosen, other)

            def then():
                waiter.answer = [*chosen, *([other] if other is not None else [])]
                waiter.done.set()

            return 200, {"v": protocol.VERSION, **data}, live.stream.conversation_id, question["turn_id"], then

        return self._request(principal, "answer_question", payload, question_id, act)

    def _close_question(self, live: _Live, question_id: str, outcome: str, chosen: list[str] | None = None,
                        other: str | None = None) -> dict:
        data = {"question_id": question_id, "outcome": outcome, "decision": protocol.decision(chosen, other)}
        if outcome == "answered":
            data["chosen"] = chosen
            if other is not None:
                data["other"] = other
        self._emit(live, lambda s: s.emit("question_closed", data))
        self.store.close_question(question_id)
        return data

    # ------------------------------------------------------------------ reading
    def status(self) -> dict:
        with self._lock:
            queue = {"active": int(self._active is not None), "waiting": len(self._queue), "limit": self.queue_limit}
            return {"v": protocol.VERSION, "service": "nuru", "version": self.version,
                    "vault": {"id": self.vault_id, "name": self.vault.name[:200]},
                    "ready": {"chat": self.chat, "notes": "ready" if self.vault.is_dir() else "unavailable", "queue": queue}}

    def request_state(self, principal: str, request_id: str, operation: str) -> dict:
        with self._lock:
            known = self.store.request(protocol.request_key(principal, self.vault_id, operation, request_id))
            if known is None:
                raise ProtocolError("not_found", "no such request")
            state = {"v": protocol.VERSION, "request_id": request_id, "operation": operation, "state": "completed"}
            if known["conversation_id"]:
                state["conversation_id"] = known["conversation_id"]
            if known["turn_id"]:
                state["turn_id"] = known["turn_id"]
            if operation == "submit_turn":
                turn = self.store.turn(known["turn_id"], principal)
                state["state"], state["last_seq"] = turn["state"], turn["last_seq"]
            return state

    def list_conversations(self, principal: str, before: str | None, limit: int) -> dict:
        with self._lock:
            rows = self.store.conversations(principal, before, min(limit, MAX_PAGE))
        return {"v": protocol.VERSION, "conversations": rows}

    def conversation(self, principal: str, conversation_id: str, before: str | None, limit: int) -> dict:
        """A conversation's turns, newest first, each with what a client shows for it."""
        with self._lock:
            conversation = self.store.conversation(conversation_id, principal)
            if conversation is None:
                raise ProtocolError("not_found", "no such conversation")
            turns = []
            for turn in self.store.turns(conversation_id, before, min(limit, MAX_PAGE)):
                display = turn["final"]
                if display is None:
                    shown = Display()
                    for event in self.store.events(turn["id"]):
                        shown.apply(event)
                    display = dataclasses.asdict(shown)
                turns.append({"turn_id": turn["id"], "text": turn["text"], "state": turn["state"], "display": display})
        return {"v": protocol.VERSION, "conversation_id": conversation_id, "title": conversation["title"],
                "created_at": conversation["created_at"], "turns": turns}

    def check_cursor(self, principal: str, turn_id: str, after: int) -> None:
        """Raise unless `turn_id` is the principal's and `after` is a cursor into its stored events."""
        with self._lock:
            turn = self.store.turn(turn_id, principal)
            if turn is None:
                raise ProtocolError("not_found", "no such turn")
            if turn["compacted"] or after < 0 or after > turn["last_seq"]:
                raise ProtocolError("cursor_expired", f"no event {after} in this turn",
                                    resync={"turn": f"/v1/conversations/{turn['conversation_id']}"})

    def wait_events(self, turn_id: str, after: int, timeout: float) -> tuple[list[dict], bool]:
        """(the stored events after `after`, whether the turn has ended), waiting up to `timeout`
        seconds for one if there are none yet. An ended turn's last event is its terminal one."""
        with self._changed:
            events = self.store.events(turn_id, after)
            if not events and turn_id in self._live and not self._closing:
                self._changed.wait(timeout)
                events = self.store.events(turn_id, after)
            return events, turn_id not in self._live

    # ------------------------------------------------------------------ the worker
    def _emit(self, live: _Live, make: Callable[[TurnStream], object]) -> list[dict]:
        """Make events on `live`'s stream and store them (with the lock held). The stream checks each
        one, so nothing a client would reject is stored or sent."""
        before = len(live.stream.events)
        try:
            make(live.stream)
        finally:
            new = live.stream.events[before:]
            if new:
                with self.store.transaction():
                    for event in new:
                        self.store.add_event(event)
                    if any(event["type"] == "started" for event in new):
                        self.store.set_turn_state(live.stream.turn_id, "running")
                    if live.stream.ended:
                        shown = Display()
                        for event in live.stream.events:
                            shown.apply(event)
                        self.store.end_turn(live.stream.turn_id, new[-1]["type"], dataclasses.asdict(shown))
                        self._live.pop(live.stream.turn_id, None)
                        if self._active == live.stream.turn_id:
                            self._active = None  # it holds the slot no longer, whatever Session does next
                self._changed.notify_all()
        return new

    def _run(self) -> None:
        try:
            while True:
                with self._lock:
                    while not self._queue and not self._closing:
                        self._changed.wait()
                    if not self._queue:
                        return
                    live = self._live[self._queue.popleft()]
                    self._active = live.stream.turn_id
                    self._emit(live, lambda s: s.emit("started", {}))
                try:
                    self._turn(live)
                finally:
                    with self._lock:
                        self._active = None
                        self._changed.notify_all()
        finally:
            if self._session is not None:
                self._session.close()

    def _turn(self, live: _Live) -> None:
        try:
            session = self._ensure_session(live.store_id)
        except Exception:
            with self._lock:
                self._emit(live, lambda s: s.emit("failed", {"code": "model_unavailable",
                                                             "message": "Nuru couldn't start its model and tools."}))
            return
        session.conversation_id = live.store_id
        session.answer_question = lambda question: self._ask_user(live, question)
        try:
            for event in session.ask(live.prompt, live.cancel):
                with self._lock:
                    if not live.stream.ended:  # after a Failure or an Ask, Session has nothing more to add
                        self._emit(live, lambda s: s.session_event(event))
        except Exception as error:
            unavailable = isinstance(error, requests.exceptions.ConnectionError)
            with self._lock:
                if not live.stream.ended:
                    for question_id in live.stream.open_questions:
                        self._close_question(live, question_id, "expired")
                    self._emit(live, lambda s: s.emit("failed", {
                        "code": "model_unavailable" if unavailable else "internal",
                        "message": "Nuru lost its model." if unavailable else "Something went wrong in Nuru."}))
            return
        finally:
            session.answer_question = None
        with self._lock:
            if not live.stream.ended:
                self._emit(live, lambda s: s.cancelled(live.cancel_reason) if live.cancel.is_set() else s.completed())

    def _ensure_session(self, conversation_id: int):
        """The shared Session, built on the first turn (in this, the worker thread)."""
        if self._session is None:
            self.chat = "loading"
            try:
                self._session = self._factory(conversation_id)
            except Exception:
                self.chat = "unavailable"
                raise
            self.chat = "ready"
        return self._session

    def _ask_user(self, live: _Live, question: Question) -> list[str] | None:
        """Session.answer_question, on the worker thread: ask through the stream and wait for an answer
        until the question expires on the host's clock. None (taken as No) if it expires or the turn
        is stopped."""
        seconds = self.answer_seconds if self.answer_seconds is not None else (question.timeout or questions.ANSWER_SECONDS)
        with self._lock:
            if live.cancel.is_set():
                return None
            expires = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            (event,) = self._emit(live, lambda s: s.question(question, _stamp(expires)))
            data = event["data"]
            self.store.add_question(data["question_id"], live.stream.turn_id, data["operation"], data["expires_at"])
            waiter = live.waiter = _Waiter(data["question_id"], data["question"], _parse(data["expires_at"]))
        waiter.done.wait(max(0.0, (waiter.expires - datetime.now(timezone.utc)).total_seconds()))
        with self._lock:
            live.waiter = None
            if not waiter.done.is_set():
                self._close_question(live, waiter.id, "expired")
                return None
            return waiter.answer
