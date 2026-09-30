"""Eval 3 - tier labels for the constrained-edit tests (suite 28-31), from a run_suite batch.

Run each test at every tier, three times, then label it:

    caffeinate -i uv run python nuru/evals/decision_layer/run_suite.py --tests 28-31 --tiers off,low,high --repeats 3 --label tiers
    uv run python nuru/evals/decision_layer/tier_labels.py .runs/suite-tiers-<time>

A test's label is the cheapest tier that passes (score_runs.PASS_MARK, 4/5) in at least two of
its three runs. Tiers are ordered as config/decision_layer.yaml orders them.
"""

import statistics
import sys
from pathlib import Path

from common import save
from laya_mcp.config import load_config
from score_runs import PASS_MARK, load_results, score


def load(batch: Path) -> list[dict]:
    return load_results(batch)


def tier_of(think: str) -> str:
    return "off" if think == "off" else "high" if think == "true" else think


def table(records: list[dict]) -> dict:
    """{test: {tier: {scores, passes, runs, seconds...}}}"""
    out: dict = {}
    for record in records:
        points, checks = score(record)
        cell = out.setdefault(record["test"], {}).setdefault(tier_of(record["tier"]), {"scores": [], "runs": [],
                                                                                         "missed": []})
        cell["scores"].append(points)
        cell["missed"].append([name for name, ok in checks if not ok])
        cell["runs"].append({"turn_seconds": record.get("turn_seconds"), "model_seconds": record.get("model_seconds"),
                             "expert_calls": record.get("expert_calls", 0), "timed_out": record.get("timed_out", False),
                             "retried": record.get("retried_without_thinking", False)})
    for tiers in out.values():
        for cell in tiers.values():
            cell["passes"] = sum(s >= PASS_MARK for s in cell["scores"])
            finished = [r["model_seconds"] for r in cell["runs"] if r["model_seconds"] is not None]
            cell["mean_score"] = round(statistics.mean(cell["scores"]), 2)
            cell["mean_model_seconds"] = round(statistics.mean(finished), 1) if finished else None
            cell["expert_calls"] = sum(r["expert_calls"] for r in cell["runs"])
    return out


def labels(results: dict, order: tuple[str, ...]) -> dict[int, str]:
    labelled = {}
    for test, tiers in results.items():
        labelled[test] = next(
            (tier for tier in order if tier in tiers and tiers[tier]["passes"] >= 2),
            "expert" if "expert" not in {t for t in tiers} else "none",
        )
    return labelled


def main():
    batch = Path(sys.argv[1])
    results = table(load(batch))
    order = load_config().tiers.order
    labelled = labels(results, order)
    path = save("tier_labels", {"batch": str(batch), "pass_mark": PASS_MARK, "results": results, "labels": labelled})
    for test in sorted(results):
        print(f"\ntest {test}  ->  label: {labelled[test]}"
              + ("   (no local tier passed; expert not run yet - Phase 4)" if labelled[test] == "expert" else ""))
        for tier in order:
            if tier in results[test]:
                c = results[test][tier]
                print(f"  {tier:5} scores {c['scores']}  passes {c['passes']}/3  model {c['mean_model_seconds']}s"
                      f"  expert calls {c['expert_calls']}")
                for missed in c["missed"]:
                    if missed:
                        print(f"        missed: {'; '.join(missed)}")
    print(f"\n{path}")


if __name__ == "__main__":
    main()
