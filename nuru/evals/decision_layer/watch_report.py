"""Compare two run_suite batches of the same tests - the thinking watch in shadow (the model's own
behaviour, with what the watch would have cut logged) and enforced - and, for the shadow batch,
what stopping at the first signal would have saved.

    uv run python nuru/evals/decision_layer/watch_report.py .runs/suite-watch-shadow-<time> .runs/suite-watch-enforce-<time>
"""

import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from common import save
from score_runs import load_results, score


def load(batch: Path) -> list[dict]:
    return load_results(batch)


def steers(batch: Path) -> dict[str, int]:
    """Stopped calls per session, from the decision log: a (session, step) with a watch decision
    marked steered. The results' model_calls can't be trusted for this - a stopped call isn't always
    in them (it has no usage figures), so counting them showed 0 where there were 8."""
    conn = sqlite3.connect(batch / "decisions.sqlite3")
    counts: dict[str, int] = defaultdict(int)
    for session_id, _ in conn.execute("SELECT DISTINCT session_id, step FROM decisions "
                                      "WHERE decision_point = 'watch_thinking' AND action_taken = 'steered'"):
        counts[session_id] += 1
    conn.close()
    return counts


def summary(records: list[dict], steered: dict[str, int]) -> dict:
    out = defaultdict(lambda: defaultdict(list))
    for r in records:
        cell = out[r["test"]]
        cell["score"].append(score(r)[0])
        cell["turn_seconds"].append(r.get("turn_seconds") or 600)
        cell["model_seconds"].append(r.get("model_seconds") or 600)
        cell["completion_tokens"].append(sum(c["completion_tokens"] for c in r.get("model_calls", [])))
        cell["steered"].append(steered.get(r.get("session_id"), 0))
        cell["timed_out"].append(bool(r.get("timed_out")))
    return out


def would_save(batch: Path) -> list[dict]:
    """Per watched call in a shadow batch: the first signal, where it fired, and where the call ended."""
    conn = sqlite3.connect(batch / "decisions.sqlite3")
    conn.row_factory = sqlite3.Row
    firsts = {}
    for row in conn.execute("SELECT * FROM decisions WHERE decision_point = 'watch_thinking' ORDER BY id"):
        key = (row["session_id"], row["step"])
        if row["status"] == "code_signal":
            fired, kind = json.loads(row["state_text"])["thinking_tokens"], row["proposed_action"]
        elif row["proposed_action"] not in (None, "progressing"):
            fired, kind = None, row["proposed_action"]  # Laya: position = the check it came at
        else:
            continue
        if key not in firsts and row["outcome"]:
            ended = int(row["outcome"].split("=")[1])
            firsts[key] = {"kind": kind, "source": "code" if row["status"] == "code_signal" else "laya",
                           "fired_at": fired, "ended_at": ended}
    return list(firsts.values())


def main():
    shadow, enforce = Path(sys.argv[1]), Path(sys.argv[2])
    arms = {"shadow (as today)": summary(load(shadow), steers(shadow)), "watch enforced": summary(load(enforce), steers(enforce))}
    tests = sorted(set().union(*[a.keys() for a in arms.values()]))
    print(f"{'test':5} {'arm':18} {'scores':12} {'turn s':>7} {'model s':>8} {'tokens':>7} {'steers':>6}")
    for test in tests:
        for name, arm in arms.items():
            c = arm.get(test)
            if not c:
                continue
            print(f"{test:<5} {name:18} {str(c['score']):12} {statistics.mean(c['turn_seconds']):7.0f} "
                  f"{statistics.mean(c['model_seconds']):8.0f} {statistics.mean(c['completion_tokens']):7.0f} "
                  f"{sum(c['steered']):6}")
    totals = {name: {"score": sum(sum(c["score"]) for c in arm.values()),
                     "of": 5 * sum(len(c["score"]) for c in arm.values()),
                     "model_seconds": round(sum(sum(c["model_seconds"]) for c in arm.values())),
                     "tokens": sum(sum(c["completion_tokens"]) for c in arm.values())} for name, arm in arms.items()}
    print()
    for name, t in totals.items():
        print(f"{name:18} score {t['score']}/{t['of']}  model time {t['model_seconds']} s  tokens {t['tokens']}")
    signals = would_save(shadow)
    print(f"\nshadow: {len(signals)} watched calls had a signal")
    for s in signals:
        saved = s["ended_at"] - s["fired_at"] if s["fired_at"] is not None else None
        print(f"  {s['source']:4} {s['kind']:10} fired at {s['fired_at']} tok, call ended at {s['ended_at']} tok"
              + (f" -> would save ~{saved}" if saved is not None else ""))
    print(save("watch_report", {"shadow": str(shadow), "enforce": str(enforce), "totals": totals,
                                "per_test": {n: {t: dict(c) for t, c in a.items()} for n, a in arms.items()},
                                "signals": signals}))


if __name__ == "__main__":
    main()
