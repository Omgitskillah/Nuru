import json

import pytest

from core import label_cli
from laya_mcp.decision_log import DecisionLog

QUESTIONS = {
    "matches_request": {"type": "noul", "instructions": "It does what was asked."},
    "verdict": {"type": "choice", "instructions": "What now?", "criteria": {"proceed": "a", "confirm": "b", "block": "c"}},
}
ANSWERS = {"matches_request": {"type": "noul", "noul": 0.9},
           "verdict": {"type": "choice", "choice": "proceed", "probabilities": {"proceed": 0.7, "confirm": 0.2, "block": 0.1}}}


@pytest.fixture
def log(tmp_path):
    log = DecisionLog(tmp_path / "d.sqlite3")
    yield log
    log.close()


def record(log, action_taken=None):
    decision_id = log.record_decision(
        decision_point="gate_write", question_set="gate_write", question_set_version=1,
        state={"request": "add a line", "tool": "append_note"}, questions=QUESTIONS, answers=ANSWERS,
        status="ok", mode="shadow", proposed_action="proceed", checkpoint="english",
    )
    if action_taken:
        log.set_action_taken(decision_id, action_taken)
    return decision_id


def run(log, replies, **kwargs):
    replies = iter(replies)
    shown = []
    done = label_cli.label(log, kwargs.get("point"), kwargs.get("limit", 20), kwargs.get("source", "user"),
                           read=lambda prompt: next(replies), out=shown.append)
    return done, "\n".join(shown)


def test_each_question_is_labelled_by_number_or_name(log):
    decision_id = record(log)
    done, shown = run(log, ["2", "confirm"])
    assert done == 1
    row = log._conn.execute("SELECT label, label_source FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    assert json.loads(row["label"]) == {"matches_request": "false", "verdict": "confirm"}
    assert row["label_source"] == "user"
    assert "Laya proposed: proceed" in shown and "(Laya 0.70)" in shown


def test_disagreements_come_first(log):
    agreement = record(log, "proceed")
    disagreement = record(log, "ask")
    _, shown = run(log, ["", "", "q"])
    assert shown.index(f"#{disagreement} ") < shown.index(f"#{agreement} ")


def test_a_skipped_question_is_left_out_and_all_skipped_leaves_it_unlabelled(log):
    first, second = record(log), record(log)
    done, _ = run(log, ["", "block", "", ""])
    assert done == 1
    labels = dict(log._conn.execute("SELECT id, label FROM decisions").fetchall())
    assert json.loads(labels[first]) == {"verdict": "block"}
    assert labels[second] is None


def test_a_wrong_answer_is_asked_again(log):
    record(log)
    done, shown = run(log, ["maybe", "9", "true", "3"])
    assert done == 1 and "not one of the options: 'maybe'" in shown


def test_claude_can_be_the_source_but_nothing_else(log):
    record(log)
    run(log, ["1", "1"], source="claude")
    assert log._conn.execute("SELECT label_source FROM decisions").fetchone()[0] == "claude"
    with pytest.raises(SystemExit):
        label_cli.main(["label", "--source", "granite"])


def test_export_writes_jsonl(log, tmp_path, capsys):
    record(log)
    run(log, ["1", "1"])
    out = tmp_path / "labels.jsonl"
    label_cli.main(["--db", str(tmp_path / "d.sqlite3"), "export", str(out)])
    assert "Wrote 1 labelled decision" in capsys.readouterr().out
    # Options are numbered as shown - in the order Laya was given them: 1 is `proceed`.
    assert json.loads(out.read_text())["label"] == json.dumps({"matches_request": "true", "verdict": "proceed"}, sort_keys=True)


def test_no_log_yet_is_a_clear_message(tmp_path):
    with pytest.raises(SystemExit, match="No decision log"):
        label_cli.main(["--db", str(tmp_path / "missing.sqlite3"), "label"])
