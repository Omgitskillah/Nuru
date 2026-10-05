"""One Nuru turn in a fresh process, for run_suite.py: start a Session against the scratch vault,
ask one prompt, write what happened as JSON.

run_suite.py sets the environment - VAULT_PATH (the scratch copy), OBSIDIAN_SEMANTIC_DB (its
pre-built index, outside the vault), NURU_DB_PATH / NURU_LOG_DIR / NURU_DECISION_CONFIG (the run's
own history, logs and decision log), PATH (with ripgrep) - before this imports core.session, which
reads them at import. The obsidian and laya servers get a restricted env from the MCP client, so
their params are patched here to carry the vault, the index and the config.
"""

import argparse
import json
import os
import time
from pathlib import Path

from mcp import StdioServerParameters
from mcp.client.stdio import get_default_environment

from core import session
import live_plan
from core.session import Failure, RetryWithoutThinking, Text, Thinking, ToolCall


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--think", required=True, help="default (the model's own level) | off | low | high - "
                        "translated by the model's profile (modelfiles/profiles.yaml)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--plan", type=Path, help="a plan (core.planner's format) for the harness to carry out "
                        "with Session.run_plan, instead of the model's own turn")
    parser.add_argument("--planner", type=Path, help="a planner Modelfile (modelfiles/planner/), swapped onto the "
                        "model to write the turn's plan live (live_plan), or 'expert[:model]' for deep-think's "
                        "write_plan (default Claude Haiku); an empty or invalid plan leaves the turn to the model")
    args = parser.parse_args()

    env = get_default_environment()
    env.update({k: os.environ[k] for k in ("VAULT_PATH", "OBSIDIAN_SEMANTIC_DB", "PATH")})
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
    reply, tool_calls, thinking, retried, failure, planned = "", [], 0, False, None, None
    try:
        plan = json.loads(args.plan.read_text()) if args.plan else None
        if args.planner:
            # Planning counts as part of the turn: it starts after `ready`, like the question.
            s.load_model()
            expert, _, expert_model = str(args.planner).partition(":")  # expert[:model]
            planned = (live_plan.make_expert(args.prompt, s.tools, expert_model or None) if expert == "expert" else
                       live_plan.make(args.prompt, args.planner, session.MODEL, session._context_window(s.model_info)))
            plan = planned["run"]
        for event in (s.run_plan(args.prompt, plan) if plan else s.ask(args.prompt)):
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
        finished = time.monotonic()
        shadow_failures = s.shadow.failures if s.shadow else None
        session_id = s.logger.session_id
    finally:
        s.close()

    model_calls = []
    for log in Path(os.environ["NURU_LOG_DIR"]).glob(f"*_{session_id}.jsonl"):
        for line in log.read_text().splitlines():
            entry = json.loads(line)
            if entry["event"] == "model_call":
                model_calls.append({k: entry[k] for k in ("duration_ms", "prompt_tokens", "completion_tokens",
                                                          "stop_reason", "tool_calls_requested")})
    args.out.write_text(json.dumps({
        "prompt": args.prompt, "think": args.think, "plan": str(args.plan) if args.plan else None, "planner": planned, "session_id": session_id, "laya_state": laya_state, "reply": reply,
        "tool_calls": tool_calls, "thinking_events": thinking, "retried_without_thinking": retried,
        "failure": failure, "startup_seconds": round(ready - started, 1), "turn_seconds": round(finished - ready, 1),
        "model_seconds": round(sum(c["duration_ms"] for c in model_calls) / 1000, 1),
        "model_calls": model_calls, "shadow_failures": shadow_failures,
        "expert_calls": sum(c["name"] == "ask_an_expert" for c in tool_calls),
    }, indent=1))


if __name__ == "__main__":
    main()
