"""The decision log: every Laya decision, the tools the harness actually ran, and the labels that
fine-tuning will need later. One SQLite file (config `database`), gitignored, never leaves this
machine - it holds state text, which can include vault content.

Written by two processes - the Laya server (decisions) and Nuru's harness (tool calls, what it
did with a decision) - so it runs in WAL mode.
"""

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    session_id TEXT,
    task_id TEXT,
    step INTEGER,
    decision_point TEXT NOT NULL,
    question_set TEXT NOT NULL,
    question_set_version INTEGER NOT NULL,
    checkpoint TEXT,
    state_hash TEXT NOT NULL,
    state_text TEXT NOT NULL,
    questions_json TEXT NOT NULL,
    answers_json TEXT,
    status TEXT NOT NULL,           -- ok | unavailable | over_budget | code_blocked | code_failed | error
    detail TEXT,
    proposed_action TEXT,           -- what the policy would do with these answers
    action_taken TEXT,              -- what the harness actually did (set by the harness)
    mode TEXT NOT NULL CHECK (mode IN ('shadow', 'enforce', 'tool')),
    latency_ms REAL,
    outcome TEXT,
    label TEXT,
    label_source TEXT CHECK (label_source IS NULL OR label_source IN ('user', 'claude')),
    labelled_at TEXT
);
CREATE INDEX IF NOT EXISTS decisions_task ON decisions (session_id, task_id, step);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    session_id TEXT,
    task_id TEXT,
    step INTEGER,
    tool TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    ok INTEGER NOT NULL,
    error TEXT,
    duration_ms REAL
);
CREATE TABLE IF NOT EXISTS expert_calls (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,              -- local date, for the daily cap
    model TEXT NOT NULL,
    stop_reason TEXT,
    ok INTEGER NOT NULL
);
"""

LABEL_SOURCES = ("user", "claude")

# Tool arguments that carry note text. The execution log keeps their size and hash, not the text:
# note IDs and paths say what was touched, and the bodies are already in the vault.
_BODY_ARGUMENTS = frozenset({"content", "body", "text"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def as_seen(value: Any) -> str:
    """JSON in the order Laya was given it. Laya reads a state's keys and a question's options in
    order and its answers change with it, so a replay or a training example must keep that order;
    `canonical` (sorted) is only for hashing."""
    return json.dumps(value, ensure_ascii=False)


def redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        key: {"chars": len(value), "sha256": sha256(value)} if key in _BODY_ARGUMENTS and isinstance(value, str) else value
        for key, value in arguments.items()
    }


class DecisionLog:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._conn.close()

    def _insert(self, table: str, row: dict[str, Any]) -> int:
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._lock:
            return self._conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", list(row.values())).lastrowid

    def record_decision(
        self,
        *,
        decision_point: str,
        question_set: str,
        question_set_version: int,
        state: Any,
        questions: dict,
        status: str,
        mode: str,
        context: dict[str, Any] | None = None,
        checkpoint: str | None = None,
        answers: dict | None = None,
        detail: str | None = None,
        proposed_action: str | None = None,
        latency_ms: float | None = None,
    ) -> int:
        context = context or {}
        state_text = state if isinstance(state, str) else as_seen(state)
        return self._insert(
            "decisions",
            {
                "ts": _now(),
                "session_id": context.get("session_id"),
                "task_id": context.get("task_id"),
                "step": context.get("step"),
                "decision_point": decision_point,
                "question_set": question_set,
                "question_set_version": question_set_version,
                "checkpoint": checkpoint,
                "state_hash": sha256(state if isinstance(state, str) else canonical(state)),
                "state_text": state_text,
                "questions_json": as_seen(questions),
                "answers_json": canonical(answers) if answers is not None else None,
                "status": status,
                "detail": detail,
                "proposed_action": proposed_action,
                "mode": mode,
                "latency_ms": latency_ms,
            },
        )

    def set_action_taken(self, decision_id: int, action: str, outcome: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE decisions SET action_taken = ?, outcome = COALESCE(?, outcome) WHERE id = ?",
                (action, outcome, decision_id),
            )

    def record_tool_call(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        ok: bool,
        error: str | None = None,
        duration_ms: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> int:
        """What the harness actually executed - never what the model said it did."""
        context = context or {}
        return self._insert(
            "tool_calls",
            {
                "ts": _now(),
                "session_id": context.get("session_id"),
                "task_id": context.get("task_id"),
                "step": context.get("step"),
                "tool": tool,
                "arguments_json": canonical(redact_arguments(arguments)),
                "ok": int(ok),
                "error": error,
                "duration_ms": duration_ms,
            },
        )

    def set_label(self, decision_id: int, label: str, source: str) -> None:
        if source not in LABEL_SOURCES:
            raise ValueError(f"label source must be one of {LABEL_SOURCES}, never the local model; got {source!r}")
        with self._lock:
            updated = self._conn.execute(
                "UPDATE decisions SET label = ?, label_source = ?, labelled_at = ? WHERE id = ?",
                (label, source, _now(), decision_id),
            ).rowcount
        if not updated:
            raise KeyError(f"no decision with id {decision_id}")

    def review_queue(self, limit: int = 20, decision_point: str | None = None) -> list[sqlite3.Row]:
        """Unlabelled decisions that Laya answered, disagreements with what was done first."""
        query = (
            "SELECT * FROM decisions WHERE label IS NULL AND status = 'ok'"
            + (" AND decision_point = ?" if decision_point else "")
            + " ORDER BY (action_taken IS NOT NULL AND action_taken != proposed_action) DESC, id LIMIT ?"
        )
        params = [decision_point, limit] if decision_point else [limit]
        return self._conn.execute(query, params).fetchall()

    def export_labelled(self, path: Path) -> int:
        """Write every labelled decision as one JSON line for fine-tuning; returns how many."""
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE label IS NOT NULL ORDER BY id"
        ).fetchall()
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(
                    canonical(
                        {
                            "id": row["id"],
                            "question_set": row["question_set"],
                            "question_set_version": row["question_set_version"],
                            "checkpoint": row["checkpoint"],
                            "state": row["state_text"],
                            "questions": json.loads(row["questions_json"]),
                            "label": row["label"],
                            "label_source": row["label_source"],
                        }
                    )
                    + "\n"
                )
        return len(rows)

    def record_expert_call(self, model: str, stop_reason: str, ok: bool) -> None:
        """One call to the cloud expert - counted whether or not it produced a plan."""
        from datetime import date

        self._insert("expert_calls", {"ts": _now(), "day": date.today().isoformat(), "model": model,
                                      "stop_reason": stop_reason, "ok": int(ok)})

    def expert_calls_on(self, day: str) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM expert_calls WHERE day = ?", (day,)).fetchone()[0]
