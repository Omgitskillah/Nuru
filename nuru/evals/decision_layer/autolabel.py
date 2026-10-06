"""Label a run_suite batch's decisions where the right answer is known from the outcome - so the data
runs produce training and calibration labels without hand-labelling every row.

The rules below are Claude's (they encode the test rubrics in score_runs.py and each test's intent),
so labels are recorded with source `claude`. A decision no rule is sure about is left for
`nuru label`. Only question-set versions this knows about are labelled (tool_gate v2, check_claim v2,
assess_effort v2, gate_write v1).

- tool_gate start `mode`: each suite prompt's intent - the four edit tests are `write`, the
  arithmetic test `answer`, every other test `read` (it needs the notes, even to find nothing).
- tool_gate next `next`: in a run that passed (4/5+), what the model did at each step - its path
  worked. Failed runs are skipped: which step went wrong isn't known.
- assess_effort `tier`: the cheapest tier that passed the test in two of its runs, across the batch
  (`tier_labels.labels`), for every decision in every run of that test. If no local tier passed,
  "expert" would be a guess, so those are left.
- gate_write: in a passing run, each write that ran is `matches_request: true`, `verdict: proceed`.
- check_claim `claim`: `no_change` for a read test that changed nothing and whose reply doesn't
  mention changing a note; `made_change` for an edit test whose write succeeded and whose reply
  says so. Anything else is left for review.

    uv run python nuru/evals/decision_layer/autolabel.py .runs/suite-<label>-<time>
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

from laya_mcp.decision_log import DecisionLog
from laya_mcp.config import load_config
from score_runs import PASS_MARK, SCORERS, score
from tier_labels import labels as tier_labels
from tier_labels import load, table

WRITE_TESTS = {28, 29, 30, 31}
ANSWER_TESTS = {34}
CHANGE_WORDS = re.compile(r"\b(added|appended|created|saved|filed|updated|archived|moved|wrote|written)\b", re.I)
VERSIONS = {"tool_gate_start": 2, "tool_gate_next": 2, "check_claim": 2, "assess_effort": 2, "gate_write": 1}


def intended_mode(test: int) -> str:
    return "write" if test in WRITE_TESTS else "answer" if test in ANSWER_TESTS else "read"


def main():
    batch = Path(sys.argv[1])
    records = load(batch)
    by_session = {r["session_id"]: r for r in records if r.get("session_id")}
    scored = {r["session_id"]: score(r)[0] for r in records if r.get("session_id") and r["test"] in SCORERS}
    cheapest = tier_labels(table([r for r in records if r["test"] in SCORERS]), load_config().tiers.order)

    log = DecisionLog(batch / "decisions.sqlite3")
    rows = log._conn.execute("SELECT * FROM decisions WHERE label IS NULL AND status = 'ok' ORDER BY id").fetchall()
    done, skipped = Counter(), Counter()
    for row in rows:
        record = by_session.get(row["session_id"])
        qset = row["question_set"]
        if record is None or "turns" in record or VERSIONS.get(qset) != row["question_set_version"]:
            skipped[qset] += 1
            continue
        test, passed = record["test"], scored.get(row["session_id"], 0) >= PASS_MARK
        label = None
        if qset == "tool_gate_start":
            label = {"mode": intended_mode(test)}
        elif qset == "tool_gate_next" and passed and row["action_taken"] in ("continue", "answer"):
            label = {"next": row["action_taken"]}
        elif qset == "assess_effort" and cheapest.get(test) in ("off", "low", "high"):  # a tier that passed
            label = {"tier": cheapest[test]}
        elif qset == "gate_write" and passed and row["action_taken"] == "executed" and row["outcome"] == "ok":
            label = {"matches_request": "true", "verdict": "proceed"}
        elif qset == "check_claim":
            reply = json.loads(row["state_text"])["reply"]
            wrote = any(c["name"] in ("write_note", "rename_or_move_file") for c in record["tool_calls"])
            if test not in WRITE_TESTS and not record["vault_changes"] and not CHANGE_WORDS.search(reply):
                label = {"claim": "no_change"}
            elif test in WRITE_TESTS and passed and wrote and CHANGE_WORDS.search(reply):
                label = {"claim": "made_change"}
        if label:
            log.set_label(row["id"], json.dumps(label, sort_keys=True), "claude")
            done[qset] += 1
        else:
            skipped[qset] += 1
    log.close()
    print(f"{batch.name}: labelled {sum(done.values())} of {len(rows)} unlabelled decisions")
    for qset in sorted(set(done) | set(skipped)):
        print(f"  {qset:18} labelled {done[qset]:4}   left for review {skipped[qset]:4}")
    print("tier labels:", cheapest)


if __name__ == "__main__":
    main()
