"""Run suite prompts through Nuru, one fresh process per case, each on a fresh scratch copy of
dummyVault - so a write test can't leak into the next, and the real vault is never touched.

    caffeinate -i uv run python nuru/evals/decision_layer/run_suite.py --tests quick          # ~15 min
    caffeinate -i uv run python nuru/evals/decision_layer/run_suite.py --tests 28-31 --tiers off,low,high --repeats 3

A batch lives in `.runs/suite-<label>-<time>/`: `results.jsonl` (one line per case: the driver's
record plus every vault file the turn changed), and the batch's own conversation history, session
logs and decision log (`decisions.sqlite3`), which the reports read. The semantic index is built
once from a pristine copy and restored before every case (see the memory notes on eval runs).
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
import manifest
from core.providers import claude as claude_provider
from cases import load_cases

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
                if p.is_file() and not any(part in SKIPPED or part.startswith(".obsidian-mcp-")
                                          for part in p.relative_to(root).parts)}

    before, after = files(DUMMY_VAULT), files(vault)
    changes = []
    for rel in sorted(set(before) | set(after)):
        old = before[rel].read_text(errors="replace") if rel in before else None
        new = after[rel].read_text(errors="replace") if rel in after else None
        if old != new:
            changes.append({"path": rel, "before": old, "after": new})
    return changes


def copy_vault(destination: Path) -> None:
    """Copy notes, excluding interactive indexes/locks and their SQLite sidecars."""
    shutil.copytree(DUMMY_VAULT, destination, symlinks=True, ignore=shutil.ignore_patterns(".obsidian-mcp-*"))


def run_turn(batch: Path, env: dict, prompt: str, think: str, timeout: int, case: dict | None = None) -> dict:
    case_path = batch / "case.json"
    if case:
        case_path.write_text(json.dumps(case))
    out = batch / "turn.json"
    out.unlink(missing_ok=True)
    logs = Path(env["NURU_LOG_DIR"])
    logs_before = set(logs.glob("*.jsonl"))

    def session_id() -> dict:
        """A turn that timed out or crashed wrote no result, but its session log names it - so its
        decisions can still be matched to it (steers, labels)."""
        new = sorted(set(logs.glob("*.jsonl")) - logs_before)
        return {"session_id": new[-1].stem.split("_", 1)[1]} if new else {}

    # A sequence gets `timeout` per turn, enforced by the driver; killing the process is only the
    # backstop, after every turn's deadline plus time for startup and a turn's slow last step.
    turn_timeout = ["--turn-timeout", str(timeout)] if case else []
    if case:
        timeout = timeout * len(case["turns"]) + 300
    with open(batch / "turn.stdout", "w") as stdout, open(batch / "turn.stderr", "w") as stderr:
        proc = subprocess.Popen(
            [sys.executable, str(HERE / "drive_turn.py"), *(["--case", str(case_path)] if case else ["--prompt", prompt]), "--think", think, "--out", str(out),
             *turn_timeout],
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



# Where Nuru reads NURU_MAIN_MODEL from when the environment doesn't set it (tests point it elsewhere).
ENV_NURU = REPO / ".env.nuru"


def require_cloud_opt_in(allow_cloud: bool) -> None:
    """Stop unless --allow-cloud was given for a batch that Nuru would run on Claude - judged from the
    model setting exactly as the session resolves it (environment, else .env.nuru), so a model chosen
    only in .env.nuru can't reach the paid API unasked."""
    if claude_provider.main_model_setting(os.environ, ENV_NURU).startswith(claude_provider.PREFIX) and not allow_cloud:
        sys.exit("NURU_MAIN_MODEL is a Claude model: pass --allow-cloud to run this batch on the paid API")


def token_summary(batch: Path) -> str:
    """Tokens in and out for the whole round and per test, from results.jsonl; also written to
    tokens.json. "in" counts every prompt token; "cached" is how many of those were Claude prompt-cache
    reads (0 on Ollama)."""
    per_test: dict[int, dict] = {}
    for line in (batch / "results.jsonl").read_text().splitlines():
        record = json.loads(line)
        totals = per_test.setdefault(record["test"], {"runs": 0, "calls": 0, "in": 0, "out": 0, "cached": 0, "cache_writes": 0})
        totals["runs"] += 1
        for call in record.get("model_calls") or []:
            totals["calls"] += 1
            totals["in"] += call.get("prompt_tokens") or 0
            totals["out"] += call.get("completion_tokens") or 0
            totals["cached"] += call.get("cache_read_tokens") or 0
            totals["cache_writes"] += call.get("cache_write_tokens") or 0
    whole = {k: sum(t[k] for t in per_test.values()) for k in ("runs", "calls", "in", "out", "cached", "cache_writes")}
    (batch / "tokens.json").write_text(json.dumps({"total": whole, "per_test": per_test}, indent=2))
    lines = [f"Tokens this round: {whole['in']:,} in ({whole['cached']:,} cached), {whole['out']:,} out, "
             f"over {whole['calls']} model calls in {whole['runs']} runs"]
    lines += [f"  test {test}: {t['in']:,} in ({t['cached']:,} cached), {t['out']:,} out, {t['calls']} calls"
              for test, t in sorted(per_test.items())]
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=HERE / "cases.json", help="additional numbered sequence cases")
    parser.add_argument("--tests", default="1-34", help='"all", "quick", or numbers and ranges: "1-34", "28-31,34"')
    parser.add_argument("--tiers", default="default", help="comma-separated thinking levels: default (the model's "
                        "own, from its profile), off, low, high")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--label", default="run")
    parser.add_argument("--timeout", type=int, default=600, help="seconds per test, or per turn of a sequence case")
    parser.add_argument("--allow-cloud", action="store_true", help="required when NURU_MAIN_MODEL is a Claude "
                        "model (anthropic:...): the batch then calls the paid Claude API, and the notes it reads "
                        "leave the machine")
    parser.add_argument("--enforce", default="", help="decision points to enforce IN THIS BATCH ONLY (comma-separated, "
                        "or 'all'); its config marks thresholds calibrated so it loads - a test of the enforce "
                        "paths, not of calibrated thresholds")
    args = parser.parse_args()

    require_cloud_opt_in(args.allow_cloud)
    prompts = suite_prompts()
    cases = load_cases(args.cases)
    if set(prompts) & set(cases):
        sys.exit("sequence case IDs overlap legacy prompts")
    tests = sorted(set(prompts) | set(cases)) if args.tests == "all" else parse_tests(args.tests)
    unknown = set(tests) - (set(prompts) | set(cases))
    if unknown:
        sys.exit(f"unknown test IDs: {sorted(unknown)}")
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
    (batch / "manifest.json").write_text(json.dumps(
        manifest.build({k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                       batch / "decision_layer.yaml"), indent=2))

    env = {**os.environ, "VAULT_PATH": str(vault), "OBSIDIAN_SEMANTIC_DB": str(index),
           "NURU_DB_PATH": str(batch / "conversation.db"), "NURU_LOG_DIR": str(batch / "logs"),
           "NURU_DECISION_CONFIG": str(batch / "decision_layer.yaml"),
           "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}
    env.pop("VIRTUAL_ENV", None)

    copy_vault(vault)
    subprocess.run(["uv", "run", "obsidian-mcp-index"], cwd=REPO / "servers" / "obsidian", env=env, check=True,
                   capture_output=True)
    shutil.copy2(index, pristine_index)

    total = len(tests) * len(args.tiers.split(",")) * args.repeats
    done = 0
    with open(batch / "results.jsonl", "a") as results:
        for test in tests:
            for think in args.tiers.split(","):
                for repeat in range(args.repeats):
                    shutil.rmtree(vault)
                    copy_vault(vault)
                    shutil.copy2(pristine_index, index)
                    started, day = time.monotonic(), date.today().isoformat()
                    prompt = prompts.get(test, cases.get(test, {}).get("turns", [{}])[0].get("prompt", ""))
                    case_env = {**env, "NURU_DB_PATH": str(batch / "history" / f"test-{test}-{think}-{repeat}.sqlite3")}
                    record = run_turn(batch, case_env, prompt, think, args.timeout, cases.get(test))
                    record.update({"test": test, "tier": think, "repeat": repeat, "prompt": prompt, "date": day,
                                   "wall_seconds": round(time.monotonic() - started, 1),
                                   "vault_changes": vault_changes(vault)})
                    results.write(json.dumps(record) + "\n")
                    results.flush()
                    done += 1
                    status = ("TIMEOUT" if record.get("timed_out") else "CRASH" if record.get("crashed")
                              else f"{len(record.get('tool_calls', []))} tools, {record.get('turn_seconds')}s")
                    print(f"[{done}/{total}] test {test} {think} #{repeat}: {status}, "
                          f"{len(record['vault_changes'])} files changed", flush=True)
    print(token_summary(batch))
    print(batch)


if __name__ == "__main__":
    main()
