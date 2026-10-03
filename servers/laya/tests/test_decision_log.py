import json

import pytest

from laya_mcp.decision_log import redact_arguments


def record(log, **overrides):
    fields = dict(
        decision_point="gate_write", question_set="gate_write", question_set_version=1,
        state={"request": "r"}, questions={"q": {"type": "noul", "instructions": "x"}},
        status="ok", mode="shadow", answers={"q": {"noul": 0.9}}, proposed_action="proceed",
        context={"session_id": "s", "task_id": "t", "step": 1},
    )
    return log.record_decision(**{**fields, **overrides})


def test_a_decision_is_stored_with_its_state_and_context(log):
    decision_id = record(log)
    row = log._conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    assert (row["session_id"], row["task_id"], row["step"]) == ("s", "t", 1)
    assert json.loads(row["state_text"]) == {"request": "r"}
    assert len(row["state_hash"]) == 64
    assert row["mode"] == "shadow"


def test_labels_come_only_from_the_user_or_claude(log):
    decision_id = record(log)
    log.set_label(decision_id, "block", "user")
    log.set_label(decision_id, "block", "claude")
    with pytest.raises(ValueError, match="never the local model"):
        log.set_label(decision_id, "block", "granite")
    with pytest.raises(KeyError):
        log.set_label(9999, "block", "user")


def test_the_database_refuses_a_model_label_source_even_written_directly(log):
    decision_id = record(log)
    with pytest.raises(Exception, match="CHECK constraint"):
        log._conn.execute("UPDATE decisions SET label_source = 'model' WHERE id = ?", (decision_id,))


def test_review_puts_disagreements_first_and_skips_labelled_and_failed_calls(log):
    agreed = record(log)
    log.set_action_taken(agreed, "proceed")
    disagreed = record(log)
    log.set_action_taken(disagreed, "ask")
    labelled = record(log)
    log.set_label(labelled, "proceed", "user")
    record(log, status="unavailable", answers=None)
    assert [r["id"] for r in log.review_queue()] == [disagreed, agreed]


def test_export_writes_labelled_decisions_as_jsonl(log, tmp_path):
    decision_id = record(log)
    record(log)
    log.set_label(decision_id, "ask", "user")
    out = tmp_path / "labels.jsonl"
    assert log.export_labelled(out) == 1
    line = json.loads(out.read_text())
    assert line["label"] == "ask" and line["question_set_version"] == 1
    assert line["questions"] == {"q": {"type": "noul", "instructions": "x"}}


def test_tool_calls_keep_ids_but_not_note_text(log):
    log.record_tool_call(tool="append_note", arguments={"id": "01ABC", "content": "secret note text"}, ok=False,
                         error="FileNotFoundError", context={"session_id": "s"})
    row = log._conn.execute("SELECT * FROM tool_calls").fetchone()
    arguments = json.loads(row["arguments_json"])
    assert arguments["id"] == "01ABC"
    assert "secret" not in row["arguments_json"] and arguments["content"]["chars"] == 16
    assert row["ok"] == 0


def test_redaction_leaves_non_text_arguments_alone():
    assert redact_arguments({"targets": ["a"], "overwrite": True}) == {"targets": ["a"], "overwrite": True}


def test_state_and_questions_are_kept_in_the_order_laya_saw_them(log):
    # Laya reads keys and options in order, and its answers change with it: a replay or a training
    # example built from a re-sorted copy would not be the decision that was made.
    state = {"request": "r", "steps": [], "failures": {}}
    questions = {"tier": {"type": "choice", "instructions": "x", "criteria": {"off": "a", "low": "b", "high": "c"}}}
    decision_id = record(log, state=state, questions=questions)
    row = log._conn.execute("SELECT state_text, questions_json, state_hash FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    assert list(json.loads(row["state_text"])) == ["request", "steps", "failures"]
    assert list(json.loads(row["questions_json"])["tier"]["criteria"]) == ["off", "low", "high"]
    reordered = record(log, state={"failures": {}, "steps": [], "request": "r"}, questions=questions)
    same = log._conn.execute("SELECT state_hash FROM decisions WHERE id = ?", (reordered,)).fetchone()
    assert same["state_hash"] == row["state_hash"]  # the hash still identifies the state
