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
"""
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIRST_ID = 101


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
    args = parser.parse_args()
    cases = json.loads((HERE / "cases.json").read_text())
    if args.command == "export":
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
