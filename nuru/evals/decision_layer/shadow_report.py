"""Eval 5 - the shadow report: what Laya would have chosen at each step, next to what the model did.

    caffeinate -i uv run python nuru/evals/decision_layer/run_suite.py --tests 1-34 --label shadow
    uv run python nuru/evals/decision_layer/shadow_report.py .runs/suite-shadow-<time>

For tool_gate, "Laya's choice" is its raw top answer (argmax) as well as what the policy would have
done with the config's (uncalibrated) thresholds. The model's is, at the start, the turn's mode as it
turned out (answer / read / write) and, after a tool result, whether it went on (continue) or replied
(answer). Written to `.runs/shadow_report-<time>.md` and printed.
"""

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from common import RUNS, save


def argmax(answers: dict, qid: str) -> str | None:
    answer = answers.get(qid)
    return answer.get("choice") if answer else None


def main():
    batch = Path(sys.argv[1])
    records = {r["session_id"]: r for r in map(json.loads, (batch / "results.jsonl").read_text().splitlines())
               if r.get("session_id")}
    conn = sqlite3.connect(batch / "decisions.sqlite3")
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM decisions ORDER BY id")]

    lines = [f"# Shadow report - {batch.name}", "",
             f"{len(records)} turns, {len(rows)} logged decisions.", ""]
    statuses = Counter((r["decision_point"], r["status"]) for r in rows)
    lines += ["## Decision calls by status", "", "| decision point | status | count |", "|---|---|---|"]
    lines += [f"| {p} | {s} | {n} |" for (p, s), n in sorted(statuses.items())]

    steps, disagreements = [], []
    for r in rows:
        if r["decision_point"] != "tool_gate" or r["status"] != "ok" or r["action_taken"] is None:
            continue
        answers = json.loads(r["answers_json"])
        qid = "mode" if r["question_set"] == "tool_gate_start" else "next"
        if qid not in answers:
            continue  # a v1 row (which tool), not comparable
        record = records.get(r["session_id"], {})
        laya, model = argmax(answers, qid), r["action_taken"]
        step = {"test": record.get("test"), "step": r["step"], "phase": r["question_set"].rsplit("_", 1)[1],
                "laya": laya, "policy": r["proposed_action"], "model": model, "agree": laya == model}
        steps.append(step)
        if not step["agree"]:
            step["laya_p"] = round(answers[qid]["probabilities"].get(laya, 0.0), 3)
            disagreements.append(step)

    agree = sum(s["agree"] for s in steps)
    lines += ["", "## tool_gate", "",
              f"Laya's top choice matched the model's at **{agree} of {len(steps)}** steps "
              f"({100 * agree / max(1, len(steps)):.0f}%).", ""]
    by_phase = Counter((s["phase"], s["agree"]) for s in steps)
    for phase in ("start", "next"):
        total = by_phase[(phase, True)] + by_phase[(phase, False)]
        lines.append(f"- {phase}: {by_phase[(phase, True)]}/{total} agree")
    lines += ["", "| test | step | phase | Laya | policy would do | model did | agree |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {s['test']} | {s['step']} | {s['phase']} | {s['laya']} | {s['policy']} | {s['model']} | "
              f"{'yes' if s['agree'] else '**no**'} |" for s in steps]

    effort = Counter((json.loads(r["answers_json"])["tier"]["choice"], r["proposed_action"], r["action_taken"])
                     for r in rows if r["decision_point"] == "assess_effort" and r["status"] == "ok")
    lines += ["", "## assess_effort", "", "| Laya's top tier | policy tier | tier used | count |", "|---|---|---|---|"]
    lines += [f"| {a} | {b} | {c} | {n} |" for (a, b, c), n in effort.most_common()]

    for point in ("gate_write", "check_claim"):
        verdicts = Counter((r["proposed_action"], r["action_taken"], r["outcome"]) for r in rows
                           if r["decision_point"] == point)
        lines += ["", f"## {point}", "", "| proposed | done | outcome | count |", "|---|---|---|---|"]
        lines += [f"| {a} | {b} | {c} | {n} |" for (a, b, c), n in verdicts.most_common()]

    report = "\n".join(lines) + "\n"
    RUNS.mkdir(exist_ok=True)
    out = RUNS / f"shadow_report-{batch.name}.md"
    out.write_text(report)
    save("shadow_report", {"batch": str(batch), "steps": steps, "agree": agree, "of": len(steps)})
    print(report)
    print(out)


if __name__ == "__main__":
    main()
