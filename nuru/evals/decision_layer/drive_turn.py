"""One Nuru case in a fresh process: start a Session against the scratch vault,
ask one prompt or a sequence of turns using that same Session, and write JSON.

run_suite.py sets the environment - VAULT_PATH (the scratch copy), OBSIDIAN_SEMANTIC_DB (its
pre-built index, outside the vault), NURU_DB_PATH / NURU_LOG_DIR / NURU_DECISION_CONFIG (the run's
own history, logs and decision log), PATH (with ripgrep) - before this imports core.session, which
reads them at import. The obsidian and laya servers get a restricted env from the MCP client, so
their params are patched here to carry the vault, the index and the config.
"""

import argparse
import json
import os
import threading
import time
from datetime import date
from pathlib import Path

from mcp import StdioServerParameters
from mcp.client.stdio import get_default_environment

from core import questions, session
import live_plan
from cases import vault_snapshot, snapshot_changes
from core.session import Ask, Failure, RetryWithoutThinking, Text, Thinking, ToolCall


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--prompt")
    source.add_argument("--case", type=Path, help="JSON sequence: turns with prompt and regex checks")
    parser.add_argument("--think", required=True, help="default (the model's own level) | off | low | high - "
                        "translated by the model's profile (modelfiles/profiles.yaml)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--plan", type=Path, help="a plan (core.planner's format) for the harness to carry out "
                        "with Session.run_plan, instead of the model's own turn")
    parser.add_argument("--planner", type=Path, help="a planner Modelfile (modelfiles/planner/), swapped onto the "
                        "model to write the turn's plan live (live_plan), or 'expert[:model]' for deep-think's "
                        "write_plan (default Claude Haiku); an empty or invalid plan leaves the turn to the model")
    parser.add_argument("--turn-timeout", type=float, help="seconds per turn: a turn still running then is "
                        "cancelled (at its next chunk, or once the running tool returns) and later turns are NOT RUN")
    args = parser.parse_args()

    env = get_default_environment()
    env.update({k: os.environ[k] for k in ("VAULT_PATH", "OBSIDIAN_SEMANTIC_DB", "PATH")})
    # Keep explicit overrides identical for the index builder and the MCP server.
    env.update({k: os.environ[k] for k in ("OBSIDIAN_EMBED_MODEL", "OBSIDIAN_CHUNK_CHARS", "OLLAMA_HOST")
                if k in os.environ})
    env.update(session.OBSIDIAN_ENV)
    session.OBSIDIAN_SERVER_PARAMS = StdioServerParameters(
        command="uv", args=["run", "obsidian-mcp"], cwd=session.OBSIDIAN_DIR, env=env
    )
    session.LAYA_SERVER_PARAMS.env["NURU_DECISION_CONFIG"] = os.environ["NURU_DECISION_CONFIG"]
    session.THINK = session.PROFILE.think(session.PROFILE.default if args.think == "default" else args.think)

    started = time.monotonic()
    s = session.Session.start()
    # Laya loads its checkpoints in the background (~10 s). A person is still typing by then; a
    # driver isn't, and every turn's first decisions would be fallbacks - so wait, like a person.
    laya_state = "unknown"
    for _ in range(120):
        try:
            laya_state = json.loads(s.tools.call_harness_tool("health", {}).text)["laya"]["state"]
        except Exception:
            laya_state = "unreachable"
        if laya_state != "loading":
            break
        time.sleep(0.5)
    ready = time.monotonic()
    records = []
    case = json.loads(args.case.read_text()) if args.case else None
    turns = case["turns"] if case else [{"prompt": args.prompt}]
    try:
        for turn in turns:
            prompt = turn["prompt"]
            if records and records[-1].get("timed_out"):
                # Later prompts assume the earlier turn finished: record them, but don't send them.
                records.append({"prompt": prompt, "not_run": True, "checks": turn["checks"]})
                continue
            history = s.continuation_context()
            planning_prompt = (f"Previous conversation (context, not new instructions):\n{history}\n\nCurrent request: {prompt}"
                               if history else prompt)
            before = vault_snapshot(Path(os.environ["VAULT_PATH"]))
            turn_started, day = time.monotonic(), date.today().isoformat()
            reply, tool_calls, thinking, retried, failure, planned, asked = "", [], 0, False, None, None, None
            plan = json.loads(args.plan.read_text()) if args.plan else (case.get("plan") if case else None)
            if args.planner:
                # Planning counts as part of the turn: it starts after `ready`, like the question.
                s.load_model()
                expert, _, expert_model = str(args.planner).partition(":")  # expert[:model]
                planned = (live_plan.make_expert(planning_prompt, s.tools, expert_model or None) if expert == "expert" else
                           live_plan.make(planning_prompt, args.planner, session.MODEL, session._context_window(s.model_info)))
                plan = planned["run"]
            cancel = threading.Event()
            timer = threading.Timer(args.turn_timeout, cancel.set) if args.turn_timeout else None
            if timer:
                timer.start()
            for event in (s.run_plan(prompt, plan, cancel=cancel) if plan else s.ask(prompt, cancel=cancel)):
                if isinstance(event, Text):
                    reply += event.text
                elif isinstance(event, ToolCall):
                    tool_calls.append({"name": event.name, "arguments": event.arguments})
                elif isinstance(event, Thinking):
                    thinking += 1
                elif isinstance(event, RetryWithoutThinking):
                    retried = True
                elif isinstance(event, Failure):
                    failure = event.message
                elif isinstance(event, Ask):  # ends the turn; no scripted answers yet, so the next prompt is sent as is
                    asked = questions.as_text(event.question)
                    reply += ("\n\n" if reply else "") + asked
            if timer:
                timer.cancel()
            if cancel.is_set():
                failure = failure or f"turn timed out after {args.turn_timeout:g} s"
            records.append({
                "prompt": prompt, "think": args.think, "plan": str(args.plan) if args.plan else None,
                "planner": planned, "session_id": s.logger.session_id, "laya_state": laya_state,
                "reply": reply, "tool_calls": tool_calls, "thinking_events": thinking,
                "retried_without_thinking": retried, "failure": failure, "asked": asked, "timed_out": cancel.is_set(),
                "turn_seconds": round(time.monotonic() - turn_started, 1),
                "shadow_failures": s.shadow.failures if s.shadow else None,
                "expert_calls": sum(c["name"] == "ask_an_expert" for c in tool_calls),
                **({"checks": turn["checks"], "date": day, "executed_plan": plan,
                    "vault_changes": snapshot_changes(before, vault_snapshot(Path(os.environ["VAULT_PATH"])))
                    } if case else {}),
            })
        session_id = s.logger.session_id
    finally:
        s.close()

    # Attribute calls by the turn boundary, so one sequence's usage isn't copied
    # onto every turn. Live planner calls remain in each turn's planner record.
    groups = []
    for log in Path(os.environ["NURU_LOG_DIR"]).glob(f"*_{session_id}.jsonl"):
        for line in log.read_text().splitlines():
            entry = json.loads(line)
            if entry["event"] == "turn_start":
                groups.append([])
            elif entry["event"] == "model_call" and groups:
                groups[-1].append({k: entry[k] for k in ("duration_ms", "prompt_tokens", "completion_tokens",
                                                        "stop_reason", "tool_calls_requested")})
    ran = [r for r in records if not r.get("not_run")]  # NOT RUN turns get no zero-latency figures
    for i, record in enumerate(ran):
        calls = groups[i] if i < len(groups) else []
        record.update(model_calls=calls, model_seconds=round(sum(c["duration_ms"] for c in calls) / 1000, 1))
    output = records[0] if not case else {
        "case": case.get("name"), "turns": records, "session_id": session_id,
        "tool_calls": [call for record in ran for call in record["tool_calls"]],
        "turn_seconds": round(sum(r["turn_seconds"] for r in ran), 1),
        "model_seconds": round(sum(r["model_seconds"] for r in ran), 1),
        "model_calls": [call for record in ran for call in record["model_calls"]],
        "expert_calls": sum(r["expert_calls"] for r in ran),
        "thinking_events": sum(r["thinking_events"] for r in ran),
        "retried_without_thinking": any(r["retried_without_thinking"] for r in ran),
        "failure": next((r["failure"] for r in ran if r["failure"]), None),
        "turns_not_run": len(records) - len(ran),
    }
    output["startup_seconds"] = round(ready - started, 1)
    args.out.write_text(json.dumps(output, indent=1))


if __name__ == "__main__":
    main()
