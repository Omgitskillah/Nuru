"""nuru-serve's host: the protocol v1 operations, with no HTTP in them (server.py adds that).

One worker thread runs every turn, one at a time, from a bounded FIFO queue. It builds the one shared
Session on the first turn (the model loads then, not at startup) and switches its conversation before
each turn with Session.use_conversation, which keeps each conversation's evidence (the note IDs its
tools have shown) apart. The HTTP threads
only store and read: they never wait on the model, so status, history, Stop and answers stay quick
while a turn runs or waits on a question.

Every turn's events are stored as they are made, before a client sees them. A turn that hadn't ended
when the service stopped ends `failed` (service_restarted) when it starts again: it's never rerun.
"""

import dataclasses
import sys
import threading
import traceback
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from importlib import metadata
from pathlib import Path
from typing import Callable

import requests

from core import questions
from core.conversation_store import ConversationStore
from core.questions import Question
from core.service import protocol
from core.service.capture import CaptureWriter
from core.service.protocol import Display, ProtocolError, TurnStream
from core.service.store import ServiceStore
from core.tool_dispatch import MUTATIONS, ToolOutcome, written_path

QUEUE_LIMIT = 4  # turns waiting behind the running one
RETENTION_DAYS = 30
MAX_PAGE = 50
RESTARTED = "Nuru restarted before this turn finished. Nothing was retried; check any write it made."
UNSAFE = "Nuru's database is in an uncertain state; restart nuru-serve"
UNKNOWN_WRITE = "Nuru stopped before this write's outcome was known. It was not retried; check the note."
UNRECORDED = "Nuru couldn't record what a tool did, so it stopped. Check the notes this turn may have changed."
REFUSED_KINDS = frozenset({"policy", "repair", "dependency", "replay"})  # ToolOutcome.kind of a call not run on purpose


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
    # Outcomes that couldn't be stored when they came, oldest first. While any is left, nothing more of
    # the turn is stored, its end above all (_caught_up); the turn has been told to stop.
    unrecorded: list[Callable[[TurnStream], None]] = dataclasses.field(default_factory=list)
    authorize: Callable[[str | None], bool] | None = None
    model: str | None = None  # the conversation's model and thinking level; None: the defaults
    thinking: str | None = None
    lost_outcome: bool = False  # an outcome couldn't be stored when it came: the turn ends failed


class Service:
    def __init__(self, store: ServiceStore, conversations: ConversationStore, session_factory: Callable[[int], object],
                 vault: Path, *, queue_limit: int = QUEUE_LIMIT, answer_seconds: float | None = None,
                 retention_days: int = RETENTION_DAYS, models: Callable[[], dict] | None = None,
                 vault_uuid: str | None = None) -> None:
        """`session_factory(conversation_id)` builds the Session (in the worker thread, on the first
        turn). `answer_seconds` overrides how long a question waits (questions.ANSWER_SECONDS).
        `models()` gives the models a conversation may pick (core.main_models.choices); without it,
        every conversation runs on the configured model."""
        self.store, self.conversations, self._factory = store, conversations, session_factory
        self._models = models
        self.vault = Path(vault).resolve()
        self.vault_id = protocol.vault_id(self.vault)  # v1's path hash
        self.vault_uuid = vault_uuid  # v2's identity (core.vault_identity), set by nuru-serve
        self.queue_limit, self.answer_seconds = queue_limit, answer_seconds
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)  # an event was stored, or the queue moved
        self._live: dict[str, _Live] = {}
        self._queue: deque[str] = deque()
        self._active: str | None = None
        self._session = None
        self.chat = "idle"  # idle, loading, ready, unavailable (status)
        self._closing = False
        # While a change is in progress (_atomic): turn ID -> (its _Live, whether it was in _live), for
        # each turn the change touched, to put back if the transaction rolls back.
        self._touched: dict[str, tuple[_Live, bool]] | None = None
        self._active_before: str | None = None
        self._unsafe = False  # a change could be neither committed nor rolled back (_atomic)
        self.captures = CaptureWriter(store, self.vault, self.vault_id, self._lock)
        try:
            self.version = metadata.version("core")
        except metadata.PackageNotFoundError:
            self.version = "dev"
        with self._lock:
            self._recover(retention_days)
            self.captures.recover()
        self._worker = threading.Thread(target=self._run, name="nuru-serve-worker", daemon=True)
        self._worker.start()

    # ------------------------------------------------------------------ startup and shutdown
    def _recover(self, retention_days: int) -> None:
        """Compact old turns, and end every turn a stop left unfinished: its questions expire, and it
        fails with service_restarted. A write it may have made stays as the conversation store left it
        (in_flight or uncertain), blocking the next write until the operator resolves it."""
        with self._atomic():
            self.store.compact(retention_days)
            for turn in self.store.unfinished_turns():
                live = _Live(TurnStream.resume(turn["conversation_id"], turn["id"], self.store.events(turn["id"])), 0, "")
                self._receipt_for_unknown_write(live, turn)
                for question_id in live.stream.open_questions:
                    self._close_question(live, question_id, "expired")
                self._emit(live, lambda s: s.emit("failed", {"code": "service_restarted", "message": RESTARTED}))

    def _receipt_for_unknown_write(self, live: _Live, turn: dict) -> None:
        """A turn cut short around a write: each write call shown without a result gets one from its
        own entry in the write ledger (looked up by the call's ID): completed is applied, with the note
        it changed; not_completed failed; in_flight or uncertain uncertain, never run again, and
        blocking further writes until the operator resolves it. A call with no entry never ran."""
        reported = {e["data"]["call_id"] for e in live.stream.events if e["type"] == "tool_result"}
        for event in list(live.stream.events):
            data = event["data"]
            if event["type"] != "tool_start" or data["name"] not in MUTATIONS or data["call_id"] in reported:
                continue
            write = self.conversations.write_for_call(data["call_id"])
            if write is None:
                continue
            state = {"completed": "applied", "not_completed": "failed"}.get(write["state"], "uncertain")
            receipt = {"call_id": data["call_id"], "tool": data["name"], "state": state}
            if state == "applied" and _vault_path(write["target"]):
                receipt["path"] = write["target"]
            if state == "uncertain":
                receipt["detail"] = UNKNOWN_WRITE

            def report(stream: TurnStream, receipt=receipt) -> None:
                stream.emit("tool_result", {"call_id": receipt["call_id"], "ok": receipt["state"] == "applied"})
                stream.emit("write_receipt", receipt)

            self._emit(live, report)

    def close_if_idle(self) -> bool:
        """Stop taking work if no turn is running or waiting, in the same step as the check, so none can be
        accepted in between. False, with nothing changed, if one is. close() still has to follow."""
        with self._lock:
            if self._active or self._queue:
                return False
            self._closing = True
            return True

    def close(self, timeout: float = 30) -> bool:
        """Stop taking work, cancel the queued turns and the running one, and wait for the worker. The
        Session is closed, never shut down: the Ollama server and its models may be someone else's.
        Returns whether the worker stopped; until it has, the database is in use and must stay owned.

        With the database unsafe (_atomic), nothing more is written: the turns in hand end when the
        next start recovers them from what was committed."""
        try:
            with self._lock:
                self._closing = True
                if not self._unsafe:
                    while self._queue:
                        live = self._live[self._queue.popleft()]
                        self._emit(live, lambda s: s.cancelled("shutdown"))
                    if self._active:
                        self._cancel_running(self._live[self._active], "shutdown")
        finally:
            with self._lock:
                self._changed.notify_all()
            self._worker.join(timeout)
        capture_idle = self.captures.stop(timeout)
        return not self._worker.is_alive() and capture_idle

    # ------------------------------------------------------------------ requests
    def _request(self, principal: str, operation: str, payload: dict, target: str, act) -> tuple[int, dict]:
        """Run `act` once per request ID. A retry with the same body and target gets the stored answer;
        the same ID for anything else is a conflict. `act` returns (status, body, conversation ID,
        turn ID, then): it runs in a transaction with the request's record, and `then` runs after the
        commit. If `act` refuses, nothing is recorded, so a retry is judged afresh."""
        key = protocol.request_key(principal, self.vault_id, operation, payload["request_id"])
        digest = protocol.payload_hash(target, payload)
        with self._lock:
            self._check_safe()
            known = self.store.request(key)
            if known:
                if known["payload_hash"] != digest:
                    raise ProtocolError("conflict", "this request_id was already used for a different request")
                body = {**known["body"], "replayed": True} if operation == "submit_turn" else known["body"]
                return known["status"], body
            if self._closing:
                raise ProtocolError("shutting_down", "Nuru is shutting down")
            with self._atomic():
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

    def submit_turn(self, principal: str, conversation_id: str, payload: dict, *, authorize=None) -> tuple[int, dict]:
        def act():
            conversation = self.store.conversation(conversation_id, principal)
            if conversation is None:
                raise ProtocolError("not_found", "no such conversation")
            prompt = self._prompt(payload)
            model, thinking = self._model_choice(conversation, payload)
            waiting = len(self._queue) + (self._active is not None)
            if waiting > self.queue_limit:
                raise ProtocolError("queue_full", f"{self.queue_limit} turns are already waiting", retry_after_seconds=5)
            turn_id = protocol.new_id()
            self.store.add_turn(turn_id, conversation_id, payload["request_id"], payload["text"])
            if (model, thinking) != (conversation["model"], conversation["thinking"]):
                self.store.set_conversation_model(conversation_id, model, thinking)
            live = _Live(TurnStream(conversation_id, turn_id), conversation["store_id"], prompt)
            live.authorize = authorize
            live.model, live.thinking = model, thinking
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

    def _model_choice(self, conversation: dict, payload: dict) -> tuple[str | None, str | None]:
        """The model and thinking level this turn runs at: what the message picks, else what the
        conversation last ran on. A new model resets thinking to its default unless one is given."""
        model, thinking = conversation["model"], conversation["thinking"]
        if "model" not in payload and "thinking" not in payload:
            return model, thinking
        if self._models is None:
            raise ProtocolError("invalid_request", "this service runs one model")
        listed = self._models()
        if "model" in payload and payload["model"] != (model or listed["default"]):
            model, thinking = payload["model"], None
        chosen = next((c for c in listed["choices"] if c["id"] == (model or listed["default"])), None)
        if chosen is None:
            raise ProtocolError("invalid_request", "model: not one this service offers")
        if "thinking" in payload:
            thinking = payload["thinking"]
            if thinking not in {level["id"] for level in chosen["thinking"]}:
                raise ProtocolError("invalid_request", "thinking: not a level this model has")
        return (None if model == listed["default"] else model), thinking

    def _prompt(self, payload: dict) -> str:
        """The message as Session gets it: the text, then what the user attached, if anything. A note is
        named, never pasted in: the model reads it with its own tools, under the same rules as any
        other read. A selection the user made is quoted."""
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
        with self._atomic():
            self._emit(live, lambda s: s.emit("question_closed", data))
            self.store.close_question(question_id)
        return data

    # ------------------------------------------------------------------ reading
    def check_target(self, principal: str, kind: str, resource_id: str) -> None:
        """URL ownership preflight, before HTTP consumes a mutation's body.

        The operation itself repeats this check under its transaction.
        """
        with self._lock:
            self._check_safe()
            lookup = {"conversation": self.store.conversation, "turn": self.store.turn,
                      "question": self.store.question}[kind]
            if lookup(resource_id, principal) is None:
                raise ProtocolError("not_found", "no such resource")

    def status(self) -> dict:
        with self._lock:
            queue = {"active": int(self._active is not None), "waiting": len(self._queue), "limit": self.queue_limit}
            body = {"v": protocol.VERSION, "service": "nuru", "version": self.version,
                    "vault": {"id": self.vault_id, "name": self.vault.name[:200]},
                    "ready": {"chat": self.chat, "notes": "ready" if self.vault.is_dir() else "unavailable", "queue": queue}}
        if self._models is not None:
            body["models"] = self._models()  # outside the lock: it may ask Ollama
        return body

    def status_v2(self) -> dict:
        """Protocol v2's status: v1's, with the vault named by its UUID instead of its path hash."""
        body = self.status()
        body["v"] = 2
        body["vault"] = {"uuid": self.vault_uuid, "name": body["vault"]["name"]}
        return body

    def request_state(self, principal: str, request_id: str, operation: str) -> dict:
        with self._lock:
            self._check_safe()
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
            self._check_safe()
            rows = self.store.conversations(principal, before, min(limit, MAX_PAGE))
        return {"v": protocol.VERSION, "conversations": rows}

    def conversation(self, principal: str, conversation_id: str, before: str | None, limit: int) -> dict:
        """A conversation's turns, newest first, each with what a client shows for it."""
        with self._lock:
            self._check_safe()
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
                "created_at": conversation["created_at"], "model": conversation["model"],
                "thinking": conversation["thinking"], "turns": turns}

    def check_cursor(self, principal: str, turn_id: str, after: int) -> None:
        """Raise unless `turn_id` is the principal's and `after` is a cursor into its stored events."""
        with self._lock:
            self._check_safe()
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
            self._check_safe()
            events = self.store.events(turn_id, after)
            if not events and turn_id in self._live and not self._closing:
                self._changed.wait(timeout)
                self._check_safe()  # it may have become unsafe while this waited
                events = self.store.events(turn_id, after)
            return events, turn_id not in self._live

    # ------------------------------------------------------------------ the worker
    @contextmanager
    def _atomic(self):
        """One change, all or nothing (with the lock held): what it stores commits together, and if it
        fails, what it changed in memory is put back as well: each touched turn's stream is rebuilt
        from its stored events, and its place in _live and the active slot are restored. Inside
        another, it joins that one. A change's effects beyond the store and these (the queue, a
        waiting worker) come after the commit (_request's `then`)."""
        self._check_safe()
        if self._touched is not None:
            yield
            return
        self._touched, self._active_before = {}, self._active
        try:
            with self.store.transaction():
                yield
        except BaseException:
            if self.store.in_transaction:  # not even rolled back: nothing it reads can be trusted now
                self._become_unsafe()
                raise
            for turn_id, (live, was_live) in self._touched.items():
                live.stream = TurnStream.resume(live.stream.conversation_id, turn_id, self.store.events(turn_id))
                if was_live:
                    self._live[turn_id] = live
            self._active = self._active_before
            raise
        finally:
            self._touched = None

    def _check_safe(self) -> None:
        if self._unsafe:
            raise ProtocolError("internal", UNSAFE)

    def _become_unsafe(self) -> None:
        """A transaction could be neither committed nor rolled back. From now on nothing touches the
        database: no request, read or event, and no more turns. The running turn is told to stop and
        its question, if any, is woken with no answer (No), all without writing; a restart recovers
        from what was committed."""
        self._unsafe = self._closing = True
        self.chat = "unavailable"
        if self._active and self._active in self._live:
            live = self._live[self._active]
            live.cancel_reason = "shutdown"
            live.cancel.set()
            if live.waiter:
                live.waiter.done.set()  # its answer stays None
        self._changed.notify_all()  # waiting readers and the worker see it
        print(f"nuru-serve: the service database couldn't roll back; {UNSAFE}", file=sys.stderr)

    def _emit(self, live: _Live, make: Callable[[TurnStream], object]) -> list[dict]:
        """Make events on `live`'s stream and store them (with the lock held). The stream checks each
        one, so nothing a client would reject is stored or sent."""
        with self._atomic():
            turn_id = live.stream.turn_id
            self._touched.setdefault(turn_id, (live, turn_id in self._live))
            before = len(live.stream.events)
            make(live.stream)
            new = live.stream.events[before:]
            for event in new:
                self.store.add_event(event)
            if any(event["type"] == "started" for event in new):
                self.store.set_turn_state(turn_id, "running")
            if new and live.stream.ended:
                shown = Display()
                for event in live.stream.events:
                    shown.apply(event)
                self.store.end_turn(turn_id, new[-1]["type"], dataclasses.asdict(shown))
                self._live.pop(turn_id, None)
                if self._active == turn_id:
                    self._active = None  # it holds the slot no longer, whatever Session does next
        if new:
            self._changed.notify_all()
        return new

    def _run(self) -> None:
        try:
            while True:
                with self._lock:
                    while not self._queue and not self._closing:
                        self._changed.wait()
                    if self._unsafe or not self._queue:
                        return
                    live = self._live[self._queue.popleft()]
                    self._active = live.stream.turn_id
                try:
                    with self._lock:
                        self._emit(live, lambda s: s.emit("started", {}))
                    self._turn(live)
                except Exception:
                    # The store failed under the turn. It is left unfinished for the next start to
                    # recover; the worker goes on, or stops if the database is now unsafe (loop head).
                    print("nuru-serve: a turn couldn't be stored:", file=sys.stderr)
                    traceback.print_exc(limit=5, file=sys.stderr)
                finally:
                    with self._lock:
                        self._active = None
                        self._changed.notify_all()
        finally:
            if self._session is not None:
                self._session.close()

    def _turn(self, live: _Live) -> None:
        if live.authorize and not live.authorize(None):
            with self._lock:
                self._emit(live, lambda s: s.emit("failed", {"code": "internal", "message": "Device authorization expired or was revoked."}))
            return
        try:
            session = self._ensure_session(live.store_id)
        except Exception as error:
            # The client only hears "couldn't start": the reason goes to the log (serve.log when managed).
            print(f"nuru-serve: couldn't start the model and tools: {type(error).__name__}: {error}", file=sys.stderr)
            traceback.print_exc(limit=8, file=sys.stderr)
            with self._lock:
                if not self._unsafe:
                    self._emit(live, lambda s: s.emit("failed", {"code": "model_unavailable",
                                                                 "message": "Nuru couldn't start its model and tools."}))
            return
        if self._unsafe:  # it became unsafe while the Session was starting
            return
        session.authorize_tool = live.authorize
        if getattr(session, "tools", None) is not None:
            session.tools.authorize_tool = live.authorize
        try:
            if hasattr(session, "use_model"):  # a Session; scripted stand-ins run one model
                session.use_model(live.model, live.thinking)
        except Exception:
            with self._lock:
                if not self._unsafe:
                    self._emit(live, lambda s: s.emit("failed", {"code": "model_unavailable",
                                                                 "message": "Nuru couldn't switch to that model."}))
            return
        session.use_conversation(live.store_id)
        session.answer_question = lambda question: self._ask_user(live, question)
        session.on_tool_outcome = lambda call_id, name, arguments, outcome, write: self._tool_outcome(live, call_id, name, outcome, write)
        try:
            for event in session.ask(live.prompt, live.cancel):
                if live.authorize and not live.authorize(None):
                    live.cancel.set()
                with self._lock:
                    # After a Failure or an Ask, Session has nothing more to add. Unsafe, it was told to
                    # stop, and what it says until then can't be stored. A Failure that ends the turn
                    # comes only after the outcomes that couldn't be stored are (_caught_up).
                    if not live.stream.ended and not self._unsafe and self._caught_up(live):
                        self._emit(live, lambda s: s.session_event(event))
        except Exception as error:
            unavailable = isinstance(error, requests.exceptions.ConnectionError)
            with self._lock:
                if not live.stream.ended and not self._unsafe and self._caught_up(live):
                    for question_id in live.stream.open_questions:
                        self._close_question(live, question_id, "expired")
                    self._emit(live, lambda s: s.emit("failed", {
                        "code": "model_unavailable" if unavailable else "internal",
                        "message": "Nuru lost its model." if unavailable else "Something went wrong in Nuru."}))
            return
        finally:
            session.answer_question = None
            session.on_tool_outcome = None
            session.authorize_tool = None
            if getattr(session, "tools", None) is not None:
                session.tools.authorize_tool = None
        with self._lock:
            if live.stream.ended or self._unsafe or not self._caught_up(live):
                return  # not caught up: left unfinished, and the next start recovers its writes from the ledger
            if live.lost_outcome:
                self._emit(live, lambda s: s.emit("failed", {"code": "internal", "message": UNRECORDED}))
            else:
                self._emit(live, lambda s: s.cancelled(live.cancel_reason) if live.cancel.is_set() else s.completed())

    def _caught_up(self, live: _Live) -> bool:
        """Store the outcomes that couldn't be stored when they came, oldest first. False if one still
        can't be: then nothing more of the turn may be stored, least of all its end. A turn left so stays
        unfinished until the next start, which gives its write calls their receipts from the write
        ledger, by call ID."""
        while live.unrecorded:
            try:
                self._emit(live, live.unrecorded[0])
            except Exception:
                return False
            live.unrecorded.pop(0)
        return True

    def _tool_outcome(self, live: _Live, call_id: str, name: str, outcome: ToolOutcome, write: bool) -> None:
        """Session.on_tool_outcome, on the worker thread: the result of the call `call_id`, and for a
        write, its receipt. Only for a call this turn's stream started and hasn't reported. If it can't
        be stored, the turn is stopped (no tool is run again) and the outcome is kept to store before
        anything else of the turn (_caught_up)."""
        def report(stream: TurnStream) -> None:
            stream.emit("tool_result", {"call_id": call_id, "ok": outcome.success,
                                        **({"summary": outcome.code[:2000]} if not outcome.success and outcome.code else {})})
            if write:
                stream.emit("write_receipt", _receipt(call_id, name, outcome))

        with self._lock:
            if self._unsafe or live.stream.ended or not _awaiting_result(live.stream, call_id):
                return
            try:
                if not self._caught_up(live):
                    raise RuntimeError("an earlier outcome is still unrecorded")
                self._emit(live, report)
            except Exception:
                print(f"nuru-serve: couldn't record the outcome of {name}; stopping the turn", file=sys.stderr)
                live.unrecorded.append(report)
                live.lost_outcome = True
                live.cancel.set()  # Session stops at its next step

    def _ensure_session(self, conversation_id: int):
        """The shared Session, built on the first turn (in this, the worker thread)."""
        if self._session is None:
            self.chat = "loading"
            try:
                self._session = self._factory(conversation_id)
            except Exception:
                self.chat = "unavailable"
                raise
            self.chat = "unavailable" if self._unsafe else "ready"
        return self._session

    def _ask_user(self, live: _Live, question: Question) -> list[str] | None:
        """Session.answer_question, on the worker thread: ask through the stream and wait for an answer
        until the question expires on the host's clock. None (taken as No) if it expires or the turn
        is stopped."""
        seconds = self.answer_seconds if self.answer_seconds is not None else (question.timeout or questions.ANSWER_SECONDS)
        with self._lock:
            if live.cancel.is_set() or self._unsafe or not self._caught_up(live):
                return None
            expires = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            with self._atomic():
                (event,) = self._emit(live, lambda s: s.question(question, _stamp(expires)))
                data = event["data"]
                self.store.add_question(data["question_id"], live.stream.turn_id, data["operation"], data["expires_at"])
            waiter = live.waiter = _Waiter(data["question_id"], data["question"], _parse(data["expires_at"]))
        waiter.done.wait(max(0.0, (waiter.expires - datetime.now(timezone.utc)).total_seconds()))
        with self._lock:
            live.waiter = None
            if self._unsafe:
                return None
            if not waiter.done.is_set():
                self._close_question(live, waiter.id, "expired")
                return None
            return waiter.answer


def _awaiting_result(stream: TurnStream, call_id: str) -> bool:
    """Whether the stream started the call `call_id` and has no result for it yet."""
    started = any(e["type"] == "tool_start" and e["data"]["call_id"] == call_id for e in stream.events)
    return started and not any(e["type"] == "tool_result" and e["data"]["call_id"] == call_id for e in stream.events)


def _vault_path(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        protocol.validate("vault_path", value)
        return True
    except ProtocolError:
        return False


def _receipt(call_id: str, tool: str, outcome: ToolOutcome) -> dict:
    """What a write actually did, for a write_receipt: applied, refused (not run on purpose: a policy,
    a No, a blocked write), failed, or uncertain (it may have run; never retried). An applied receipt
    names the note the notes server reports it changed, never the name the call asked for."""
    if outcome.success:
        state = "applied"
    elif outcome.certainty == "uncertain":
        state = "uncertain"
    elif outcome.kind in REFUSED_KINDS:
        state = "refused"
    else:
        state = "failed"
    receipt = {"call_id": call_id, "tool": tool, "state": state}
    if state == "applied" and _vault_path(target := written_path(tool, outcome)):
        receipt["path"] = target
    if state != "applied" and outcome.code:
        receipt["detail"] = outcome.code[:500]
    return receipt
