"""Run suite prompts through Nuru, one fresh process per turn, each on a fresh scratch copy of
dummyVault - so a write test can't leak into the next, and the real vault is never touched.

    caffeinate -i uv run python nuru/evals/decision_layer/run_suite.py --tests quick          # ~15 min
    caffeinate -i uv run python nuru/evals/decision_layer/run_suite.py --tests 28-31 --tiers off,low,high --repeats 3

A batch lives in `.runs/suite-<label>-<time>/`: `results.jsonl` (one line per turn: the driver's
record plus every vault file the turn changed), and the batch's own conversation history, session
logs and decision log (`decisions.sqlite3`), which the reports read. The semantic index is built
once from a pristine copy and restored before every turn (see the memory notes on eval runs).
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import yaml

from common import DUMMY_VAULT, HERE, REPO, RUNS

# The quick suite: one test per behaviour worth watching, each ~1-2 minutes, and each one has failed
# in at least one earlier scored run (so it tells setups apart) - except 34, the no-tool control.
#   3 search before answering (1/5, 0/5)      4 find the right daily note (0/5, 0/5)
#  10 search, don't claim no access (0/5, 5/5) 24 prompt injection (5/5, 0/5)
#  26 destructive request (2/5, 4/5)          28, 29 writes (2/5, 4/5; 5/5, 4/5)
#  34 arithmetic, no tool needed (control)
# Scores are 2026-09-19 and 2026-09-21. The full 34 stay the occasional regression check.
QUICK = [3, 4, 10, 24, 26, 28, 29, 34]

SKIPPED = (".obsidian", ".obsidian-mcp-index.sqlite3", ".obsidian-mcp-semantic.sqlite3", ".trash",
           ".obsidian-mcp-write.lock")


def suite_prompts() -> dict[int, str]:
    """The 34 scored prompts, from the full-results table of the 2026-09-21 run."""
    prompts = {}
    for line in (REPO / "nuru-test-results-2026-09-21.md").read_text().splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 3 and cells[1].isdigit() and int(cells[1]) not in prompts:
            prompts[int(cells[1])] = cells[2]
        if len(prompts) == 34:
            break
    return prompts


def parse_tests(spec: str) -> list[int]:
    if spec == "quick":
        return list(QUICK)
    tests = []
    for part in spec.split(","):
        low, _, high = part.partition("-")
        tests += range(int(low), int(high or low) + 1)
    return tests


def vault_changes(vault: Path) -> list[dict]:
    """Every note the turn created, changed or removed, relative to pristine dummyVault."""
    def files(root):
        return {p.relative_to(root).as_posix(): p for p in root.rglob("*")
                if p.is_file() and not any(part in SKIPPED for part in p.relative_to(root).parts)}

    before, after = files(DUMMY_VAULT), files(vault)
    changes = []
    for rel in sorted(set(before) | set(after)):
        old = before[rel].read_text(errors="replace") if rel in before else None
        new = after[rel].read_text(errors="replace") if rel in after else None
        if old != new:
            changes.append({"path": rel, "before": old, "after": new})
    return changes


def run_turn(batch: Path, env: dict, prompt: str, think: str, timeout: int) -> dict:
    out = batch / "turn.json"
    out.unlink(missing_ok=True)
    logs = Path(env["NURU_LOG_DIR"])
    logs_before = set(logs.glob("*.jsonl"))

    def session_id() -> dict:
        """A turn that timed out or crashed wrote no result, but its session log names it - so its
        decisions can still be matched to it (steers, labels)."""
        new = sorted(set(logs.glob("*.jsonl")) - logs_before)
        return {"session_id": new[-1].stem.split("_", 1)[1]} if new else {}

    with open(batch / "turn.stdout", "w") as stdout, open(batch / "turn.stderr", "w") as stderr:
        proc = subprocess.Popen(
            [sys.executable, str(HERE / "drive_turn.py"), "--prompt", prompt, "--think", think, "--out", str(out)],
            env=env, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, start_new_session=True, cwd=REPO,
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)  # the MCP servers too: same session
            proc.wait()
            return {"timed_out": True, "timeout_seconds": timeout, **session_id()}
    if proc.returncode or not out.exists():
        return {"crashed": True, "returncode": proc.returncode, "stderr_tail": (batch / "turn.stderr").read_text()[-3000:],
                **session_id()}
    return json.loads(out.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests", default="1-34", help='"quick", or numbers and ranges: "1-34", "28-31,34"')
    parser.add_argument("--tiers", default="low", help="comma-separated: off,low,high (think values)")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--label", default="run")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--enforce", default="", help="decision points to enforce IN THIS BATCH ONLY (comma-separated, "
                        "or 'all'); its config marks thresholds calibrated so it loads - a test of the enforce "
                        "paths, not of calibrated thresholds")
    args = parser.parse_args()

    prompts = suite_prompts()
    batch = RUNS / f"suite-{args.label}-{datetime.now():%Y%m%d-%H%M%S}"
    vault, index, pristine_index = batch / "vault", batch / "semantic.sqlite3", batch / "semantic.pristine.sqlite3"
    batch.mkdir(parents=True)

    config = yaml.safe_load((REPO / "config" / "decision_layer.yaml").read_text())
    config["database"] = str(batch / "decisions.sqlite3")
    header = ""
    if args.enforce:
        points = list(config["decision_points"]) if args.enforce == "all" else args.enforce.split(",")
        for point in points:
            config["decision_points"][point] = "enforce"
        for options in config["thresholds"].values():
            for thresholds in options.values():
                for threshold in thresholds.values():
                    threshold["calibration"] = {"date": datetime.now().date(), "labels": 0, "held_out": 1}
        header = ("# TEST ONLY - written by run_suite.py --enforce for one batch. The thresholds are NOT\n"
                  "# calibrated; they are marked so only so that this config loads with enforce on.\n")
    (batch / "decision_layer.yaml").write_text(header + yaml.safe_dump(config, sort_keys=False))

    env = {**os.environ, "VAULT_PATH": str(vault), "OBSIDIAN_SEMANTIC_DB": str(index),
           "NURU_DB_PATH": str(batch / "conversation.db"), "NURU_LOG_DIR": str(batch / "logs"),
           "NURU_DECISION_CONFIG": str(batch / "decision_layer.yaml"),
           "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}
    env.pop("VIRTUAL_ENV", None)

    shutil.copytree(DUMMY_VAULT, vault, symlinks=True)
    subprocess.run(["uv", "run", "obsidian-mcp-index"], cwd=REPO / "servers" / "obsidian", env=env, check=True,
                   capture_output=True)
    shutil.copy2(index, pristine_index)

    total = len(parse_tests(args.tests)) * len(args.tiers.split(",")) * args.repeats
    done = 0
    with open(batch / "results.jsonl", "a") as results:
        for test in parse_tests(args.tests):
            for think in args.tiers.split(","):
                for repeat in range(args.repeats):
                    shutil.rmtree(vault)
                    shutil.copytree(DUMMY_VAULT, vault, symlinks=True)
                    shutil.copy2(pristine_index, index)
                    started, day = time.monotonic(), date.today().isoformat()
                    record = run_turn(batch, env, prompts[test], think, args.timeout)
                    record.update({"test": test, "tier": think, "repeat": repeat, "prompt": prompts[test], "date": day,
                                   "wall_seconds": round(time.monotonic() - started, 1),
                                   "vault_changes": vault_changes(vault)})
                    results.write(json.dumps(record) + "\n")
                    results.flush()
                    done += 1
                    status = ("TIMEOUT" if record.get("timed_out") else "CRASH" if record.get("crashed")
                              else f"{len(record.get('tool_calls', []))} tools, {record.get('turn_seconds')}s")
                    print(f"[{done}/{total}] test {test} {think} #{repeat}: {status}, "
                          f"{len(record['vault_changes'])} files changed", flush=True)
    print(batch)


if __name__ == "__main__":
    main()
