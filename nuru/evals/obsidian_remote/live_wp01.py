"""WP01 M5 live-model lane: six frozen cases (live_cases.json) x N repetitions, each through Session
directly (as the CLI and Qt window use it) and through nuru-serve and the plugin's client
(plugins/obsidian/bench/follow.js), in alternating order. Qwen3.5 4B; the semantic index uses
nomic-embed-text-v2-moe. Needs Ollama, Docker (run_python) and approval: it calls the model.

    uv run --locked python nuru/evals/obsidian_remote/live_wp01.py [--repeats 5] [--cases read,arithmetic]

Every run starts from the same vault bytes (NuruPluginTestVault) and the same pre-built index, with its
own conversation database, logs and decision log under the batch folder; nothing touches the real
vault or databases. Each run is scored on its last reply (the case's regexes) and on the exact files it
changed; a change outside the case's allowed set is an unauthorized write and fails acceptance.
Writes runs.jsonl and summary.json into the batch folder (default .runs/obsidian-remote/live-<time>, ignored by git).
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "nuru" / "tests"))

import plugin_test_vault  # noqa: E402

ROUTES = ("direct", "plugin")
BASE_MODEL = "qwen3.5:4b"  # the plan's one model for this lane
EMBED_MODEL = "nomic-embed-text-v2-moe"


# --------------------------------------------------------------------------- one direct run (child process)
def child(case_path: Path, out: Path) -> None:
    """Session in this process, as the CLI and Qt window use it; the environment is the batch's."""
    from core import session

    session.LAYA_SERVER_PARAMS.env["NURU_DECISION_CONFIG"] = os.environ["NURU_DECISION_CONFIG"]
    case = json.loads(case_path.read_text())
    begun = time.monotonic()
    s = session.Session.start(vault=Path(os.environ["VAULT_PATH"]))
    questions = []

    def answer(question):
        questions.append(question.text)
        return [case["answer"]] if case["answer"] else None

    s.answer_question = answer
    receipts = []
    s.on_tool_outcome = lambda call_id, name, arguments, outcome, write: write and receipts.append(
        {"state": "applied" if outcome.success else outcome.certainty, "tool": name})
    turns = []
    try:
        for text in case["turns"]:
            reply, tools, state, message = "", [], "completed", ""
            questions.clear(), receipts.clear()
            for event in s.ask(text):
                if isinstance(event, session.Text):
                    reply += event.text
                elif isinstance(event, session.ToolCall):
                    tools.append({"name": event.name, "arguments": event.arguments})
                elif isinstance(event, session.Failure):
                    state, message = "failed", event.message
                elif isinstance(event, session.Ask):
                    questions.append(event.question.text)
            turns.append({"text": text, "reply": reply, "state": state, "message": message, "tools": tools,
                          "questions": len(questions), "receipts": list(receipts), "seconds": time.monotonic() - begun})
            begun = time.monotonic()
    finally:
        s.close()
    out.write_text(json.dumps({"turns": turns}, ensure_ascii=False))


# --------------------------------------------------------------------------- the routes
def run_direct(batch: Path, env: dict, case: dict, timeout: int) -> dict:
    (batch / "case.json").write_text(json.dumps(case, ensure_ascii=False))
    out = batch / "direct.json"
    out.unlink(missing_ok=True)
    with open(batch / "run.stderr", "a") as stderr:
        process = subprocess.Popen([sys.executable, __file__, "--child", str(batch / "case.json"), str(out)], env=env,
                                   stdout=stderr, stderr=stderr, stdin=subprocess.DEVNULL, start_new_session=True, cwd=ROOT)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)  # the MCP servers too
            process.wait()
            return {"timed_out": True}
    if process.returncode or not out.exists():
        return {"crashed": True, "returncode": process.returncode}
    return json.loads(out.read_text())


SERVE = ("import os, sys\nfrom core import session\n"
         "session.LAYA_SERVER_PARAMS.env['NURU_DECISION_CONFIG'] = os.environ['NURU_DECISION_CONFIG']\n"
         "from core.service.server import main\nmain(sys.argv[1:])\n")


def run_plugin(batch: Path, env: dict, case: dict, timeout: int) -> dict:
    """nuru-serve (with the batch's decision log, as the direct route) and the plugin's client."""
    shutil.rmtree(env["NURU_SERVICE_DIR"], ignore_errors=True)
    subprocess.run([sys.executable, "-c", SERVE, "token", "add", "obsidian"], env=env, cwd=ROOT, check=True, capture_output=True)
    home = batch / "home"
    (home / ".nuru/service/clients").mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(env["NURU_SERVICE_DIR"]) / "clients" / "obsidian.token", home / ".nuru/service/clients/obsidian.token")
    stderr = open(batch / "run.stderr", "a")
    serve = subprocess.Popen([sys.executable, "-c", SERVE, "--port", "0"], env=env, cwd=ROOT, stdout=stderr,
                             stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, start_new_session=True, text=True)
    try:
        port = None
        for line in serve.stderr:
            stderr.write(line)
            if match := re.search(r"http://127\.0\.0\.1:(\d+)", line):
                port = match.group(1)
                break
        if port is None:
            return {"crashed": True, "returncode": serve.wait()}
        threading.Thread(target=lambda: [stderr.write(line) for line in serve.stderr], daemon=True).start()
        follow = subprocess.Popen(["node", str(ROOT / "plugins/obsidian/bench/follow.js"), port, env["VAULT_PATH"], str(home)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True,
                                  cwd=ROOT / "plugins/obsidian")
        try:
            output, _ = follow.communicate(json.dumps({"op": "live", "turns": case["turns"], "answer": case["answer"]}) + "\n",
                                           timeout=timeout)
        except subprocess.TimeoutExpired:
            follow.kill()
            return {"timed_out": True}
        return json.loads(output.splitlines()[0]) if follow.returncode == 0 and output.strip() else {"crashed": True}
    finally:
        serve.send_signal(signal.SIGTERM)
        try:
            serve.wait(30)
        except subprocess.TimeoutExpired:
            os.killpg(serve.pid, signal.SIGKILL)
            serve.wait()
        stderr.close()


# --------------------------------------------------------------------------- scoring
def score(case: dict, result: dict, changed: dict, vault: Path) -> dict:
    if "turns" not in result:
        return {"pass": False, "unauthorized_write": bool(sum(map(len, changed.values()))), "why": ["timed out" if result.get("timed_out") else "crashed"]}
    reply = result["turns"][-1]["reply"]
    why = [f"missing {p}" for p in case["must"] if not re.search(p, reply)]
    why += [f"has {p}" for p in case["must_not"] if re.search(p, reply)]
    allowed = case["changes"]
    unauthorized = {kind: [p for p in changed[kind] if p not in allowed[kind]] for kind in changed}
    why += [f"{kind} {p} (unauthorized)" for kind, paths in unauthorized.items() for p in paths]
    verbs = {"added": "add", "changed": "change", "removed": "remove"}
    why += [f"didn't {verbs[kind]} {p}" for kind in allowed for p in allowed[kind] if p not in changed[kind]]
    for path, patterns in case.get("content", {}).items():
        text = (vault / path).read_text() if (vault / path).exists() else ""
        why += [f"{path} lacks {p}" for p in patterns if not re.search(p, text)]
    why += [f"turn {i + 1} {t['state']}" for i, t in enumerate(result["turns"]) if t["state"] != "completed"]
    return {"pass": not why, "unauthorized_write": any(unauthorized.values()), "why": why}


# --------------------------------------------------------------------------- the batch
def ollama_digests() -> dict:
    import requests

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    models = requests.get(f"{host if '://' in host else 'http://' + host}/api/tags", timeout=10).json()["models"]
    return {m["name"]: m["digest"] for m in models if m["name"].split(":")[0] in {"nuru", "qwen3.5", EMBED_MODEL}}


def diff_sha256(batch: Path) -> str:
    """The uncommitted code a batch ran, saved beside it (the diff of tracked files, then each new file
    whole), and its hash. Reads git only: the index is left as it is."""
    def git(*args):
        return subprocess.run(["git", *args], capture_output=True, cwd=ROOT, check=True).stdout

    diff = git("diff", "HEAD", "--binary")
    for name in git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if name:
            diff += b"\n=== new file " + name + b"\n" + (ROOT / name.decode()).read_bytes()
    (batch / "uncommitted.diff").write_bytes(diff)
    return hashlib.sha256(diff).hexdigest()


def manifest(args, batch: Path, cases: dict, pristine: dict) -> dict:
    def out(*command):
        return subprocess.run(command, capture_output=True, text=True, cwd=ROOT).stdout.strip()
    from core import session

    return {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "command": " ".join(sys.argv),
            "commit": out("git", "rev-parse", "HEAD"), "dirty": bool(out("git", "status", "--porcelain")),
            "diff_sha256": diff_sha256(batch),
            "base_model": session.BASE_MODEL, "model": session.MODEL, "max_num_ctx": session.MAX_NUM_CTX,
            "think": session.THINK, "modelfile_sha256": hashlib.sha256(session._modelfile_for(session.BASE_MODEL).read_bytes()).hexdigest(),
            "embed_model": EMBED_MODEL, "ollama_digests_before": ollama_digests(),
            "cases_sha256": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
            "vault_sha256": hashlib.sha256(json.dumps(pristine, sort_keys=True).encode()).hexdigest(),
            "node": out("node", "--version"), "settings": {k: str(v) for k, v in vars(args).items()}}


def summarise(records: list[dict], cases: list[dict]) -> dict:
    summary = {}
    for route in ROUTES:
        mine = [r for r in records if r["route"] == route]
        per_case = {}
        for case in cases:
            runs = [r for r in mine if r["case"] == case["id"]]
            passed = [r["seconds"] for r in runs if r["score"]["pass"]]
            per_case[case["id"]] = {"runs": len(runs), "passed": len(passed),
                                   "median_seconds_when_correct": sorted(passed)[len(passed) // 2] if passed else None,
                                   "failures": [r["score"]["why"] for r in runs if not r["score"]["pass"]]}
        summary[route] = {"passed": sum(r["score"]["pass"] for r in mine), "runs": len(mine),
                          "unauthorized_writes": sum(r["score"]["unauthorized_write"] for r in mine),
                          "timeouts_or_crashes": sum(bool(r["result"].get("timed_out") or r["result"].get("crashed")) for r in mine),
                          "cases": per_case}
    summary["acceptance"] = "fail: unauthorized write" if any(r["score"]["unauthorized_write"] for r in records) else "no unauthorized writes"
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--cases", help="comma-separated case IDs (default: all six)")
    parser.add_argument("--timeout", type=int, default=900, help="seconds per run")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--child", nargs=2, type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        return child(*args.child)
    os.environ["NURU_BASE_MODEL"] = BASE_MODEL  # before core.session is imported (manifest), and for every run

    cases = json.loads((HERE / "live_cases.json").read_text())["cases"]
    if args.cases:
        cases = [c for c in cases if c["id"] in args.cases.split(",")]
    batch = (args.out or ROOT / ".runs" / "obsidian-remote" / f"live-{datetime.now():%Y%m%d-%H%M%S}").resolve()
    batch.mkdir(parents=True)
    pristine_dir = batch / "pristine"
    pristine = plugin_test_vault.snapshot(plugin_test_vault.build(pristine_dir))
    vault, index, pristine_index = batch / plugin_test_vault.NAME, batch / "semantic.sqlite3", batch / "semantic.pristine.sqlite3"
    config = yaml.safe_load((ROOT / "config" / "decision_layer.yaml").read_text())
    config["database"] = str(batch / "decisions.sqlite3")
    (batch / "decision_layer.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    env = {**os.environ, "VAULT_PATH": str(vault), "OBSIDIAN_SEMANTIC_DB": str(index), "OBSIDIAN_EMBED_MODEL": EMBED_MODEL,
           "NURU_DB_PATH": str(batch / "conversation.db"), "NURU_LOG_DIR": str(batch / "logs"),
           "NURU_DECISION_CONFIG": str(batch / "decision_layer.yaml"), "NURU_SERVICE_DIR": str(batch / "service"),
           "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}
    env.pop("VIRTUAL_ENV", None)
    shutil.copytree(pristine_dir / plugin_test_vault.NAME, vault)
    subprocess.run(["uv", "run", "obsidian-mcp-index"], cwd=ROOT / "servers" / "obsidian", env=env, check=True, capture_output=True)
    shutil.copy2(index, pristine_index)
    (batch / "manifest.json").write_text(json.dumps(manifest(args, batch, cases, pristine), indent=1))

    records, total = [], args.repeats * len(cases) * len(ROUTES)
    with open(batch / "runs.jsonl", "a") as runs:
        for repeat in range(args.repeats):
            for number, case in enumerate(cases):
                order = ROUTES if (repeat + number) % 2 == 0 else ROUTES[::-1]
                for route in order:
                    shutil.rmtree(vault)
                    shutil.copytree(pristine_dir / plugin_test_vault.NAME, vault)
                    shutil.copy2(pristine_index, index)
                    run_env = {**env, "NURU_DB_PATH": str(batch / "history" / f"{case['id']}-{route}-{repeat}.db")}
                    (batch / "history").mkdir(exist_ok=True)
                    started = time.monotonic()
                    result = (run_direct if route == "direct" else run_plugin)(batch, run_env, case, args.timeout)
                    seconds = round(time.monotonic() - started, 1)
                    changed = plugin_test_vault.changes(pristine, plugin_test_vault.snapshot(vault))
                    record = {"case": case["id"], "route": route, "repeat": repeat, "order": order.index(route),
                              "seconds": round(sum(t["seconds"] for t in result.get("turns", [])), 1) or seconds,
                              "wall_seconds": seconds, "changes": changed, "result": result,
                              "score": score(case, result, changed, vault)}
                    records.append(record)
                    runs.write(json.dumps(record, ensure_ascii=False) + "\n")
                    runs.flush()
                    print(f"[{len(records)}/{total}] {case['id']} {route} #{repeat}: "
                          f"{'PASS' if record['score']['pass'] else 'FAIL ' + '; '.join(record['score']['why'])} ({record['seconds']} s)", flush=True)
    summary = summarise(records, cases)
    summary["ollama_digests_after"] = ollama_digests()
    summary["runs_sha256"] = hashlib.sha256((batch / "runs.jsonl").read_bytes()).hexdigest()
    (batch / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    for route in ROUTES:
        print(f"{route}: {summary[route]['passed']}/{summary[route]['runs']} passed, "
              f"{summary[route]['unauthorized_writes']} unauthorized writes, {summary[route]['timeouts_or_crashes']} timeouts/crashes")
    print(summary["acceptance"], batch)


if __name__ == "__main__":
    main()
