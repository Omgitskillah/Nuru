"""Bridge between this package's cases and decision_layer/run_suite.py.

    python3 nuru/evals/multi_turn/suite_adapter.py export --out <scratch>/mt-core.json
    NURU_BASE_MODEL=qwen3.5:4b NURU_MAX_NUM_CTX=16384 caffeinate -i -s uv run python \
        nuru/evals/decision_layer/run_suite.py --cases <scratch>/mt-core.json --tests 101-112 --tiers off --label mt-pilot
    python3 nuru/evals/multi_turn/suite_adapter.py records <batch> --out <batch>/mt-records.json
    python3 nuru/evals/multi_turn/mechanical_checks.py --records <batch>/mt-records.json

export writes the core cases as run_suite sequence cases, numbered from 101 in cases.json order. Each
turn keeps only its prompt and an empty checks dict, so no grading metadata reaches the run. Controlled
cases (MT12-14) need fault adapters or a context preflight that the runner does not execute, so they are
refused rather than run without their setup.

records turns a batch's results.jsonl into the per-turn records mechanical_checks.py reads. Each turn's
vault_changes are cumulative from the original fixture to the end of that turn. A turn without evidence
(NOT RUN, or a process that timed out or crashed) gets no reply or vault_changes, so its screens are INVALID.

The failure pairs (failure_pairs/cases.json) compare a legacy one-shot prompt with a multi-turn sequence
that ends on that exact prompt:

    python3 nuru/evals/multi_turn/suite_adapter.py export-pairs --out <scratch>/fp.json [--variant plain]
    ... run_suite.py --cases <scratch>/fp.json --tests 206-232 ...          # the multi-turn arm
    ... run_suite.py --tests 6,8,9,12,13,17,20,21,25,30,31,32 ...           # the one-shot arm
    uv run python nuru/evals/multi_turn/suite_adapter.py compare-pairs <multi-turn batch> <one-shot batch>

export-pairs numbers each pair 200 + its legacy test. --variant plain swaps in the preparation turns from
failure_pairs/plain-variants.json: what a person would naturally say, without restrictions or method hints. compare-pairs scores the multi-turn arm's final reply
with that test's legacy scorer, using the whole conversation's tool calls and vault diff, so both arms are
scored the same way. Time to result is the whole conversation's turn time.
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIRST_ID = 101
PAIRS_OFFSET = 200


def core_cases(cases: dict) -> dict[int, str]:
    """Suite number -> case id, for every core case in file order."""
    core = [case_id for case_id, case in cases.items() if case["mode"] == "core"]
    return {FIRST_ID + i: case_id for i, case_id in enumerate(core)}


def export(cases: dict, only: list[str] | None = None) -> dict:
    numbers = core_cases(cases)
    if only:
        refused = [c for c in only if c not in cases or cases[c]["mode"] != "core"]
        if refused:
            raise ValueError(f"not core cases (unknown, or controlled with a setup the runner can't apply): {refused}")
    return {str(number): {"name": case_id,
                          "turns": [{"prompt": t["prompt"], "checks": {}} for t in cases[case_id]["turns"]]}
            for number, case_id in numbers.items() if not only or case_id in only}


def export_pairs(pairs: dict, plain: dict | None = None) -> dict:
    """The failure pairs' multi-turn arms as run_suite sequence cases, prompts only: the scaffolded
    multi_turn, or with `plain` (plain-variants.json) its preparation turns followed by the one-shot prompt."""
    def prompts(case):
        if plain is None:
            return [t["prompt"] for t in case["multi_turn"]]
        preparation = plain["preparation_turns"][case["id"]]
        if len(preparation) + 1 != len(case["multi_turn"]):
            raise ValueError(f"{case['id']}: plain variant has {len(preparation) + 1} turns, scaffolded {len(case['multi_turn'])}")
        return [*preparation, case["one_shot_prompt"]]
    return {str(PAIRS_OFFSET + case["original_test"]): {
                "name": case["id"], "turns": [{"prompt": p, "checks": {}} for p in prompts(case)]}
            for case in pairs["cases"]}


def as_one_shot(result: dict) -> dict:
    """A multi-turn arm's result as a record its legacy test's scorer reads: the final reply, judged with
    the whole conversation's tool calls and vault diff. A conversation that didn't reach a finished final
    turn scores like a one-shot run that timed out."""
    turns = result.get("turns") or []
    last = turns[-1] if turns else {}
    unfinished = (result.get("timed_out") or result.get("crashed") or not turns
                  or last.get("not_run") or last.get("timed_out"))
    # Without "turns": score_runs.score() sends any record with turns to the sequence checks instead of
    # the legacy test's scorer.
    record = {key: value for key, value in result.items() if key != "turns"}
    return {**record, "test": result["test"] - PAIRS_OFFSET, "reply": last.get("reply", ""),
            "failure": last.get("failure"), "timed_out": bool(unfinished)}


def compare_pairs(mt_batch: Path, os_batch: Path) -> str:
    sys.path.insert(0, str(HERE.parent / "decision_layer"))
    import score_runs
    arms = {"one-shot": {}, "multi-turn": {}}
    for record in score_runs.load_results(os_batch):
        arms["one-shot"].setdefault(record["test"], []).append(record)
    for record in score_runs.load_results(mt_batch):
        if record["test"] > PAIRS_OFFSET:
            one_shot = as_one_shot(record)
            arms["multi-turn"].setdefault(one_shot["test"], []).append(one_shot)
    names = {c["original_test"]: c["id"] for c in json.loads((HERE / "failure_pairs" / "cases.json").read_text())["cases"]}
    lines = ["| Pair | One-shot score | Multi-turn score | One-shot median s | Multi-turn median s (all turns) |",
             "|---|---|---|---|---|"]
    totals = {arm: 0.0 for arm in arms}
    for test in sorted(t for t in names if t in arms["multi-turn"]):
        cells = []
        for arm in arms:
            runs = arms[arm].get(test, [])
            mean = statistics.mean(score_runs.score(r)[0] for r in runs) if runs else None
            totals[arm] += mean or 0
            cells.append((mean, statistics.median(r.get("turn_seconds") or r.get("wall_seconds") or 0 for r in runs)
                          if runs else None, len(runs)))
        (os_mean, os_s, os_n), (mt_mean, mt_s, mt_n) = cells
        fmt = lambda v, d: "-" if v is None else f"{v:.{d}f}"
        lines.append(f"| {names[test]} (test {test}) | {fmt(os_mean, 1)} (n={os_n}) | {fmt(mt_mean, 1)} (n={mt_n}) "
                     f"| {fmt(os_s, 0)} | {fmt(mt_s, 0)} |")
    lines.append(f"| **Total** | **{totals['one-shot']:.1f}** | **{totals['multi-turn']:.1f}** | | |")
    return "\n".join(lines)


def cumulative(turns: list[dict]) -> list[list[dict] | None]:
    """Per-turn changes since each turn started -> changes since the original fixture, per turn."""
    original, current, out = {}, {}, []
    for turn in turns:
        if not isinstance(turn.get("vault_changes"), list):
            out.append(None)  # nothing after a turn without a snapshot is trustworthy
            continue
        if None in out:
            out.append(None)
            continue
        for change in turn["vault_changes"]:
            original.setdefault(change["path"], change["before"])
            current[change["path"]] = change["after"]
        out.append([{"path": p, "before": original[p], "after": current[p]}
                    for p in sorted(current) if original[p] != current[p]])
    return out


def records(results: list[dict], cases: dict) -> list[dict]:
    numbers = core_cases(cases)
    out = []
    for result in results:
        case_id = numbers.get(result.get("test"))
        if case_id is None:
            continue
        turns = result.get("turns") or [{} for _ in cases[case_id]["turns"]]  # a killed process left none
        for number, (turn, changes) in enumerate(zip(turns, cumulative(turns)), 1):
            record = {"case_id": case_id, "turn": number, "test": result["test"], "repeat": result.get("repeat")}
            if not turn.get("not_run") and isinstance(turn.get("reply"), str):
                record["reply"] = turn["reply"]
            if changes is not None and not turn.get("not_run"):
                record["vault_changes"] = changes
            out.append(record)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    exporting = commands.add_parser("export")
    exporting.add_argument("--out", type=Path, required=True)
    exporting.add_argument("--only", help="comma-separated core case ids, e.g. MT01,MT08")
    reading = commands.add_parser("records")
    reading.add_argument("batch", type=Path)
    reading.add_argument("--out", type=Path, required=True)
    pairing = commands.add_parser("export-pairs")
    pairing.add_argument("--out", type=Path, required=True)
    pairing.add_argument("--variant", choices=("scaffolded", "plain"), default="scaffolded",
                         help="plain: failure_pairs/plain-variants.json, no restrictions or method hints")
    comparing = commands.add_parser("compare-pairs")
    comparing.add_argument("multi_turn_batch", type=Path)
    comparing.add_argument("one_shot_batch", type=Path)
    args = parser.parse_args()
    cases = json.loads((HERE / "cases.json").read_text())
    if args.command == "export-pairs":
        plain = (json.loads((HERE / "failure_pairs" / "plain-variants.json").read_text())
                 if args.variant == "plain" else None)
        exported = export_pairs(json.loads((HERE / "failure_pairs" / "cases.json").read_text()), plain)
        args.out.write_text(json.dumps(exported, indent=1))
        names = ", ".join(f"{number} {case['name']}" for number, case in exported.items())
        print(f"{len(exported)} pairs: {names}")
    elif args.command == "compare-pairs":
        print(compare_pairs(args.multi_turn_batch, args.one_shot_batch))
    elif args.command == "export":
        try:
            exported = export(cases, args.only.split(",") if args.only else None)
        except ValueError as error:
            parser.error(str(error))
        args.out.write_text(json.dumps(exported, indent=1))
        names = ", ".join(f"{number} {case['name']}" for number, case in exported.items())
        print(f"{len(exported)} cases: {names}")
    else:
        results = [json.loads(line) for line in (args.batch / "results.jsonl").read_text().splitlines() if line.strip()]
        args.out.write_text(json.dumps(records(results, cases), indent=1))


if __name__ == "__main__":
    main()
