"""Replay logged decisions through Laya - no model runs, about a minute for a full suite's worth.

Every shadow run stored the exact state Laya saw at each decision point. This asks Laya again, on
those states, with the question sets as they are now (or from `--questions`, a folder of variants
to try without touching the real ones), and compares:

- against the labels (`nuru label`), where there are any: accuracy per question, old and new;
- for tool_gate, against what the model actually did - a proxy while labels are few;
- against the old answers: how many top answers changed.

Options that were filled in at request time (the tool list, subject labels) are taken from the log,
so a tool_gate replay offers the same tools the model had. Only a change to the model, its Modelfile
or Nuru's loop needs a live run (run_suite.py); a change to Laya's side - question wording, the
checkpoint, calibration - can be judged here.

    uv run python nuru/evals/decision_layer/replay.py .runs/suite-shadow-<time> [more batches or .sqlite3 files]
    uv run python nuru/evals/decision_layer/replay.py nuru/.db/decisions.sqlite3 --questions my_variants/
"""

import argparse
import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from common import save

from laya_mcp.config import load_config
from laya_mcp.predictor import LayaPredictor
from laya_mcp.question_sets import QUESTIONS_DIR, load_question_sets


def logged_rows(source: Path) -> list[dict]:
    db = source / "decisions.sqlite3" if source.is_dir() else source
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM decisions WHERE status IN ('ok', 'over_budget') ORDER BY id")]
    conn.close()
    return rows


def questions_now(qset, logged: dict) -> dict:
    """The set's current questions, with request-time options taken from the logged ones."""
    questions = {}
    for qid, q in qset.questions.items():
        body = {"type": q.type, "instructions": q.instructions}
        criteria = logged.get(qid, {}).get("criteria") if q.criteria_from else q.criteria
        if criteria is None and q.type == "choice":
            continue  # a question the old version didn't ask, with options only the request knew
        if criteria is not None:
            body["criteria"] = criteria
        questions[qid] = body
    return questions


def top(answer: dict | None) -> str | None:
    if not answer:
        return None
    if answer.get("type") == "noul":
        return "true" if answer["noul"] >= 0.5 else "false"
    return answer.get("choice") if answer.get("type") == "choice" else str(round(answer.get("score", 0)))


def model_did(row: dict) -> str | None:
    """For a tool_gate row: what the model actually did, in the terms of the question asked - the
    turn's mode (answer/read/write), or whether it went on (continue) or replied (answer)."""
    taken = row["action_taken"]
    if not taken or ":" in taken or row["question_set_version"] < 2:
        return None  # v1 asked which tool; those rows can't be compared with v2's options
    return taken


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="+", type=Path, help="run_suite batch folders or decision-log .sqlite3 files")
    parser.add_argument("--questions", type=Path, default=QUESTIONS_DIR, help="folder of question-set YAML to try")
    parser.add_argument("--only", help="replay only this question set")
    args = parser.parse_args()

    sets = load_question_sets(args.questions)
    rows = [r for s in args.sources for r in logged_rows(s)
            if r["question_set"] in sets and (not args.only or r["question_set"] == args.only)]
    predictor = LayaPredictor(load_config().laya)
    predictor.start()
    while predictor.status()["state"] == "loading":
        time.sleep(0.2)
    if predictor.status()["state"] != "ready":
        raise SystemExit(predictor.status())

    stats = defaultdict(lambda: defaultdict(int))
    started = time.monotonic()
    for row in rows:
        qset = sets[row["question_set"]]
        logged_q = json.loads(row["questions_json"])
        old = json.loads(row["answers_json"]) if row["answers_json"] else {}
        label = json.loads(row["label"]) if row["label"] else {}
        state = json.loads(row["state_text"]) if row["state_text"].startswith(("{", "[")) else row["state_text"]
        questions = questions_now(qset, logged_q)
        s = stats[row["question_set"]]
        s["rows"] += 1
        try:
            new = predictor.predict(qset, state, questions).answers
        except Exception as e:
            s["failed: " + type(e).__name__] += 1
            continue
        for qid in questions:
            s[f"{qid}: top answer changed"] += top(old.get(qid)) is not None and top(old.get(qid)) != top(new.get(qid))
            if qid in label:
                s[f"{qid}: labelled"] += 1
                s[f"{qid}: correct before"] += top(old.get(qid)) == label[qid]
                s[f"{qid}: correct now"] += top(new.get(qid)) == label[qid]
        if row["decision_point"] == "tool_gate" and model_did(row):
            qid = "mode" if row["question_set"] == "tool_gate_start" else "next"
            s["matches the model: before"] += top(old.get(qid)) == model_did(row)
            s["matches the model: now"] += top(new.get(qid)) == model_did(row)
            s["model moves"] += 1

    report = {name: dict(values) for name, values in stats.items()}
    path = save("replay", {"sources": [str(s) for s in args.sources], "questions": str(args.questions),
                           "seconds": round(time.monotonic() - started, 1), "report": report})
    print(f"replayed {len(rows)} decisions in {time.monotonic() - started:.0f}s with {args.questions}\n")
    for name, values in report.items():
        print(name)
        for key, value in sorted(values.items()):
            print(f"  {key:40} {value}")
    print(f"\n{path}")


if __name__ == "__main__":
    main()
