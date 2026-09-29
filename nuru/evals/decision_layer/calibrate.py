"""Suggest thresholds from labelled decisions, checked on held-out data.

For every threshold in config/decision_layer.yaml: the labelled decisions are split by session, 70%
to fit and 30% held out (the same session never lands in both). On the fitting part, the threshold
is the lowest probability at which the option, when Laya gives it at least that probability, is the
right answer at least `--precision` of the time (and at least `--min-support` times). That threshold
is then measured on the held-out part only. An option no threshold makes that reliable is reported
as such: it must keep falling back to its safe default. Nothing is written to the config.

    uv run python nuru/evals/decision_layer/calibrate.py .runs/suite-data-<time> [more batches]
"""

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

from common import save
from laya_mcp.config import load_config
from laya_mcp.question_sets import load_question_sets

GRID = [round(x, 2) for x in np.arange(0.30, 1.00, 0.01)]


def rows(batches: list[Path]) -> list[dict]:
    out = []
    for batch in batches:
        conn = sqlite3.connect(batch / "decisions.sqlite3")
        conn.row_factory = sqlite3.Row
        out += [dict(r) for r in conn.execute(
            "SELECT session_id, question_set, question_set_version, answers_json, label FROM decisions "
            "WHERE label IS NOT NULL AND status = 'ok'")]
        conn.close()
    return out


def held_out(session_id: str) -> bool:
    return int(hashlib.sha256(session_id.encode()).hexdigest(), 16) % 10 < 3


def probability(answer: dict, option: str) -> float:
    if answer["type"] == "noul":
        return answer["noul"] if option == "true" else 1 - answer["noul"]
    return answer["probabilities"].get(option, 0.0)


def precision_at(pairs, t):
    chosen = [right for p, right in pairs if p >= t]
    return (sum(chosen) / len(chosen) if chosen else None), len(chosen)


def ece(conf: list[float], correct: list[bool], bins: int = 10) -> float:
    conf, correct = np.array(conf), np.array(correct, dtype=float)
    total = 0.0
    for lo in np.linspace(0, 1, bins, endpoint=False):
        mask = (conf >= lo) & (conf < lo + 1 / bins)
        if mask.any():
            total += mask.sum() / len(conf) * abs(conf[mask].mean() - correct[mask].mean())
    return round(float(total), 3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("batches", nargs="+", type=Path)
    parser.add_argument("--precision", type=float, default=0.90)
    parser.add_argument("--min-support", type=int, default=5)
    args = parser.parse_args()

    config, sets = load_config(), load_question_sets()
    labelled = [r for r in rows(args.batches) if r["question_set"] in sets
                and r["question_set_version"] == sets[r["question_set"]].version]
    by_question = defaultdict(list)  # (qset, qid) -> [(answer, label option, held out)]
    for r in labelled:
        answers, label = json.loads(r["answers_json"]), json.loads(r["label"])
        for qid, option in label.items():
            if qid in answers:
                by_question[(r["question_set"], qid)].append((answers[qid], option, held_out(r["session_id"])))

    results = []
    for qset, questions in config.thresholds.items():
        for qid, options in questions.items():
            data = by_question.get((qset, qid), [])
            calib = ([a.get("answer_confidence", max(a.get("probabilities", {"x": a.get("noul", 0.5)}).values())) for a, _, _ in data],
                     [(a.get("choice") or ("true" if a.get("noul", 0) >= 0.5 else "false")) == o for a, o, _ in data])
            label_options = sorted({o for _, o, _ in data})
            for option in options:
                targets = label_options if option == "any" else [option]
                for target in targets:
                    fit = [(probability(a, target), o == target) for a, o, h in data if not h]
                    test = [(probability(a, target), o == target) for a, o, h in data if h]
                    positives = sum(right for _, right in fit + test)
                    suggestion = next((t for t in GRID if (lambda pr, n: pr is not None and pr >= args.precision and n >= args.min_support)(*precision_at(fit, t))), None)
                    held_precision, held_n = precision_at(test, suggestion) if suggestion is not None else (None, 0)
                    held_positives = sum(right for _, right in test)
                    results.append({
                        "question_set": qset, "question": qid, "option": target, "config_key": option,
                        "labels": len(data), "fit": len(fit), "held_out": len(test), "positives": positives,
                        "current": config.threshold(qset, qid, target),
                        "suggested": suggestion,
                        "held_out_precision": None if held_precision is None else round(held_precision, 3),
                        "held_out_coverage": None if suggestion is None or not held_positives
                        else round(sum(1 for p, right in test if p >= suggestion and right) / held_positives, 3),
                        "held_out_answered": held_n,
                        "ece": ece(*calib) if data else None,
                    })

    path = save("calibrate", {"date": date.today().isoformat(), "precision_target": args.precision,
                              "batches": [str(b) for b in args.batches], "results": results})
    print(f"{len(labelled)} labelled decisions; target precision {args.precision}\n")
    print(f"{'question':32} {'option':14} {'labels':>6} {'pos':>4} {'now':>5} {'suggest':>8} {'held-out prec':>13} {'coverage':>9}  ECE")
    for r in results:
        name = f"{r['question_set']}.{r['question']}"
        print(f"{name:32} {r['option']:14} {r['labels']:6} {r['positives']:4} {r['current'] or '-':>5} "
              f"{r['suggested'] if r['suggested'] is not None else 'none':>8} "
              f"{r['held_out_precision'] if r['held_out_precision'] is not None else '-':>13} "
              f"{r['held_out_coverage'] if r['held_out_coverage'] is not None else '-':>9}  {r['ece']}")
    print(f"\n{path}")


if __name__ == "__main__":
    main()
