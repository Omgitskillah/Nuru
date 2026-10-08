"""Compare two run_suite batches on the same tests - the model's own turn against the harness
carrying out a plan (run_suite.py --plans) - for accuracy (score_runs) and speed.

    uv run python nuru/evals/decision_layer/compare_arms.py .runs/suite-free-... .runs/suite-plan-...

Per test: mean score out of 5, passes (4/5 or better), median turn time and model calls per turn.
Turn time is from the question to the reply (driver's turn_seconds); a timeout or crash scores 0.
"""

import statistics
import sys
from collections import defaultdict
from pathlib import Path

from score_runs import PASS_MARK, load_results, score


def summarise(batch: Path) -> dict[int, dict]:
    runs = defaultdict(list)
    for record in load_results(batch):
        runs[record["test"]].append(record)
    out = {}
    for test, records in runs.items():
        points = [score(r)[0] for r in records]
        finished = [r for r in records if not (r.get("timed_out") or r.get("crashed"))]
        out[test] = {
            "runs": len(records),
            "mean": statistics.mean(points),
            "passes": sum(p >= PASS_MARK for p in points),
            "seconds": [r["turn_seconds"] for r in finished],
            "calls": [len(r.get("model_calls", [])) for r in finished],
            "tokens": [sum(c["prompt_tokens"] + c["completion_tokens"] for c in r.get("model_calls", [])) for r in finished],
        }
    return out


def median(values: list) -> float | None:
    return statistics.median(values) if values else None


def fmt(value, suffix="") -> str:
    return "-" if value is None else f"{value:.1f}{suffix}" if isinstance(value, float) else f"{value}{suffix}"


def main():
    free, plan = (Path(arg) for arg in sys.argv[1:3])
    a, b = summarise(free), summarise(plan)
    tests = sorted(set(a) & set(b))
    print("| Test | Free: score | Plan: score | Free: passes | Plan: passes | Free: median s | Plan: median s "
          "| Free: model calls | Plan: model calls |")
    print("|---|---|---|---|---|---|---|---|---|")
    for t in tests:
        x, y = a[t], b[t]
        print(f"| {t} | {x['mean']:.1f} | {y['mean']:.1f} | {x['passes']}/{x['runs']} | {y['passes']}/{y['runs']} "
              f"| {fmt(median(x['seconds']), ' s')} | {fmt(median(y['seconds']), ' s')} "
              f"| {fmt(median(x['calls']))} | {fmt(median(y['calls']))} |")
    for label, s in (("Free", a), ("Plan", b)):
        seconds = [v for t in tests for v in s[t]["seconds"]]
        tokens = [v for t in tests for v in s[t]["tokens"]]
        print(f"\n{label}: mean score {statistics.mean(s[t]['mean'] for t in tests):.2f}/5, "
              f"passes {sum(s[t]['passes'] for t in tests)}/{sum(s[t]['runs'] for t in tests)}, "
              f"median turn {fmt(median(seconds), ' s')}, "
              f"p90 {fmt(statistics.quantiles(seconds, n=10)[-1] if len(seconds) > 1 else None, ' s')}, "
              f"median tokens per turn {fmt(median(tokens))}")


if __name__ == "__main__":
    main()
