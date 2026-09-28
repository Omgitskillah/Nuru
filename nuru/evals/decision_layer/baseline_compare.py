"""Eval 4 - baseline (always `high`) against the assess_effort policy, from a tier_labels batch.

Before the enforcement paths exist (Phase 4) Nuru can't yet run *at* the tier assess_effort picks,
so this is a replay: the batch already ran every test at every local tier, and its shadow log holds
what assess_effort predicted for each test's first step. The candidate's score and latency for a
test are the batch's measured ones at the tier the policy would have picked. Two candidates:

- policy: the config's thresholds applied (what `assess_effort` returns; uncalibrated, so mostly the
  default tier - which is exactly the fail-safe behaviour);
- argmax: Laya's single most likely tier, thresholds ignored - what calibration would unlock.

What the replay can't show: escalation on failure signals (the tier going up mid-task) and the
expert tier, both Phase 4. Re-run this comparison for real once they exist.

    uv run python nuru/evals/decision_layer/baseline_compare.py .runs/suite-tiers-<time>
"""

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from common import save
from tier_labels import load, table


def first_step_predictions(batch: Path, records: list[dict]) -> dict[int, dict]:
    """Per test: the policy's tier and Laya's argmax tier for the first step, over all its runs."""
    conn = sqlite3.connect(batch / "decisions.sqlite3")
    conn.row_factory = sqlite3.Row
    out = {}
    for test in sorted({r["test"] for r in records}):
        sessions = [r["session_id"] for r in records if r["test"] == test and r.get("session_id")]
        rows = conn.execute(
            f"SELECT proposed_action, answers_json, status FROM decisions WHERE decision_point = 'assess_effort' "
            f"AND step = 0 AND session_id IN ({','.join('?' * len(sessions))})", sessions,
        ).fetchall()
        argmax = [json.loads(r["answers_json"])["tier"]["choice"] for r in rows if r["answers_json"]]
        out[test] = {
            "policy": Counter(r["proposed_action"] for r in rows).most_common(1)[0][0] if rows else None,
            "argmax": Counter(argmax).most_common(1)[0][0] if argmax else None,
            "statuses": dict(Counter(r["status"] for r in rows)),
        }
    return out


def main():
    batch = Path(sys.argv[1])
    records = load(batch)
    results = table(records)
    predictions = first_step_predictions(batch, records)

    def arm(pick) -> dict:
        per_test = {}
        for test, tiers in results.items():
            tier = pick(test)
            cell = tiers.get(tier)
            per_test[test] = {"tier": tier, "mean_score": cell["mean_score"] if cell else None,
                              "mean_model_seconds": cell["mean_model_seconds"] if cell else None,
                              "expert_calls": cell["expert_calls"] if cell else None}
        measured = [v for v in per_test.values() if v["mean_score"] is not None]
        return {"per_test": per_test,
                "total_score": round(sum(v["mean_score"] for v in measured), 2),
                "total_model_seconds": round(sum(v["mean_model_seconds"] or 0 for v in measured), 1),
                "expert_calls": sum(v["expert_calls"] or 0 for v in measured),
                "unmeasured": [t for t, v in per_test.items() if v["mean_score"] is None]}

    arms = {
        "baseline (high)": arm(lambda test: "high"),
        "assess_effort policy": arm(lambda test: predictions[test]["policy"]),
        "assess_effort argmax": arm(lambda test: predictions[test]["argmax"]),
    }
    path = save("baseline_compare", {"batch": str(batch), "predictions": predictions, "arms": arms})
    print("first-step predictions:", json.dumps(predictions))
    for name, a in arms.items():
        per = ", ".join(f"{t}:{v['tier']}={v['mean_score']}" for t, v in a["per_test"].items())
        print(f"{name:22} score {a['total_score']:5} /{5 * len(results)}   model time {a['total_model_seconds']:6}s   "
              f"expert calls {a['expert_calls']}   [{per}]"
              + (f"   unmeasured: {a['unmeasured']}" if a["unmeasured"] else ""))
    print(f"\n{path}")


if __name__ == "__main__":
    main()
