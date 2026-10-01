"""What a batch (or several) says about training Laya - run after autolabel.py.

For each question Laya was asked, against the labels the batch produced:

- label yield and class balance: how many labels, spread over which options (a question whose
  labels are nearly all one option teaches little, and a model trained on it learns the prior);
- zero-shot accuracy and calibration (ECE) of Laya's top choice - the baseline fine-tuning has to
  beat - and the confusion table: which options it mistakes for which;
- confident mistakes: wrong with probability >= 0.7, the most useful examples to train on, listed
  by decision id;
- the thinking watch: each stopped call against how its run scored, so a stop can be labelled
  good (the run passed) or doubtful (it failed);
- with batches from two models, how their tool paths differ per test: the first tool, rounds per
  turn and pass rate - candidates for strategy labels (the expert teacher-labelling idea).

    uv run python nuru/evals/decision_layer/laya_metrics.py .runs/suite-full-8b-x5-<time> [.runs/suite-full-low-x10-<time>]
"""

import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from common import save
from score_runs import PASS_MARK, SCORERS, load_results, score

CONFIDENT = 0.7


def labelled(batch: Path) -> list[dict]:
    conn = sqlite3.connect(batch / "decisions.sqlite3")
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, session_id, question_set, answers_json, label FROM decisions WHERE label IS NOT NULL AND status = 'ok'")]
    conn.close()
    return rows


def top(answer: dict) -> tuple[str | None, float]:
    if answer.get("type") == "noul":
        p = answer.get("noul", 0.5)
        return ("true", p) if p >= 0.5 else ("false", 1 - p)
    probabilities = answer.get("probabilities") or {}
    if not probabilities:
        return None, 0.0
    option = max(probabilities, key=probabilities.get)
    return option, probabilities[option]


def ece(pairs: list[tuple[float, bool]], bins: int = 10) -> float | None:
    if not pairs:
        return None
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        cell = [(p, ok) for p, ok in pairs if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if cell:
            total += len(cell) / len(pairs) * abs(statistics.mean(p for p, _ in cell) - statistics.mean(ok for _, ok in cell))
    return round(total, 3)


def question_metrics(rows: list[dict]) -> dict:
    by_question: dict[str, list] = defaultdict(list)
    for row in rows:
        answers, label = json.loads(row["answers_json"] or "{}"), json.loads(row["label"])
        for qid, option in label.items():
            if qid in answers:
                by_question[f"{row['question_set']}.{qid}"].append((row["id"], answers[qid], str(option)))
    out = {}
    for key, items in sorted(by_question.items()):
        guesses = [(did, *top(answer), option) for did, answer, option in items]
        correct = [g == option for _, g, _, option in guesses]
        confusion = Counter((option, g) for _, g, _, option in guesses)
        out[key] = {
            "labels": len(items),
            "balance": dict(Counter(option for *_, option in items).most_common()),
            "accuracy": round(sum(correct) / len(correct), 3),
            "ece": ece([(p, ok) for (_, _, p, _), ok in zip(guesses, correct)]),
            "confusion (label -> laya)": {f"{a} -> {b}": n for (a, b), n in confusion.most_common() if a != b},
            "confident_mistakes": [did for did, g, p, option in guesses if g != option and p >= CONFIDENT],
        }
    return out


def watch_metrics(batch: Path, scores: dict[str, int]) -> dict:
    """Each stopped call (a session and step the watch steered), and whether its run passed."""
    conn = sqlite3.connect(batch / "decisions.sqlite3")
    stops = conn.execute(
        "SELECT DISTINCT session_id, step, proposed_action FROM decisions WHERE decision_point = 'watch_thinking' "
        "AND action_taken = 'steered' AND status = 'code_signal'").fetchall()
    conn.close()
    kinds: dict[str, Counter] = defaultdict(Counter)
    for session_id, _, kind in stops:
        passed = scores.get(session_id)
        kinds[kind]["run passed" if passed is not None and passed >= PASS_MARK else "run failed or unknown"] += 1
    return {kind: dict(counts) for kind, counts in kinds.items()}


def paths(records: list[dict]) -> dict[int, dict]:
    """Per test: the most common first tool, mean tool rounds, pass rate."""
    out: dict[int, dict] = {}
    by_test: dict[int, list] = defaultdict(list)
    for r in records:
        by_test[r["test"]].append(r)
    for test, runs in sorted(by_test.items()):
        firsts = Counter((r.get("tool_calls") or [{"name": "(none)"}])[0]["name"] for r in runs)
        rounds = [sum(1 for m in r.get("model_calls", []) if m.get("tool_calls_requested")) for r in runs]
        points = [score(r)[0] for r in runs] if test in SCORERS else []
        out[test] = {
            "first_tool": firsts.most_common(1)[0][0],
            "rounds": round(statistics.mean(rounds), 1),
            "pass_rate": round(sum(p >= PASS_MARK for p in points) / len(points), 2) if points else None,
            "mean_score": round(statistics.mean(points), 2) if points else None,
        }
    return out


def main():
    batches = [Path(b) for b in sys.argv[1:]]
    report = {}
    for batch in batches:
        records = load_results(batch)
        scores = {r["session_id"]: score(r)[0] for r in records if r.get("session_id") and r["test"] in SCORERS}
        report[batch.name] = {
            "questions": question_metrics(labelled(batch)),
            "watch_stops": watch_metrics(batch, scores),
            "paths": paths(records),
        }

    for name, data in report.items():
        print(f"\n== {name}")
        print(f"{'question':34} {'labels':>6} {'acc':>5} {'ECE':>5} {'conf. wrong':>11}  balance")
        for key, m in data["questions"].items():
            balance = ", ".join(f"{k} {v}" for k, v in list(m["balance"].items())[:4])
            print(f"{key:34} {m['labels']:6} {m['accuracy']:5} {str(m['ece']):>5} {len(m['confident_mistakes']):11}  {balance}")
        print("watch stops:", data["watch_stops"] or "none")

    if len(report) == 2:
        (a, da), (b, db) = report.items()
        print(f"\n== paths: {a} | {b}  (first tool, rounds, pass rate)")
        for test in sorted(set(da["paths"]) | set(db["paths"])):
            pa, pb = da["paths"].get(test, {}), db["paths"].get(test, {})
            differs = " <- different first tool" if pa.get("first_tool") != pb.get("first_tool") else ""
            print(f"{test:>3}  {pa.get('first_tool')}, {pa.get('rounds')}, {pa.get('pass_rate')} | "
                  f"{pb.get('first_tool')}, {pb.get('rounds')}, {pb.get('pass_rate')}{differs}")

    print(save("laya_metrics", {"batches": [str(b) for b in batches], "report": report}))


if __name__ == "__main__":
    main()
