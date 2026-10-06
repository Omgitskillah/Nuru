"""Test 32 with the evidence handed over: is the audit answerable from the notes Nuru reads?

Claude Sonnet 5.5 gets test 32's prompt and the seven notes Qwen read across its test 32 runs
(dummyVault data only), no tools, no Nuru. If it reaches the rubric's verdict - periodic attach
meets 3 years without PSM; the biggest risk is the report rate - Nuru's failure is its reasoning;
if not, the test itself is unclear. No local model runs.

    uv run python nuru/evals/decision_layer/reference_check_32.py --repeats 3
"""

import argparse
import functools
import json
from datetime import datetime
from pathlib import Path

import anthropic

from common import DUMMY_VAULT, RUNS
from core.session import expert_api_key  # the same key deep-think uses
from run_suite import suite_prompts
from score_runs import score

MODEL = "claude-sonnet-5-5"
NOTES = [
    "nuru/battery-life-modeling-revised.md",
    "nuru/battery-life-modeling.md",
    "shared/debugging-session-psm-not-engaging.md",
    "user/correction-the-47-minute-mystery.md",
    "shared/Projects/area-monitor-device-redesign.md",
    "user/daily-2026-09-17.md",
    "user/daily-2026-09-18.md",
]
SYSTEM = ("You are a personal assistant answering from the user's Obsidian notes, given below. Answer from the "
          "notes only, cite note paths, and say plainly what the notes leave unresolved.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    prompt = suite_prompts()[32]
    notes = "\n\n".join(f"<note path=\"{p}\">\n{(DUMMY_VAULT / p).read_text()}\n</note>" for p in NOTES)
    client = anthropic.Anthropic(api_key=expert_api_key())
    # Streamed: a long audit with adaptive thinking needs more output room than a plain request allows
    # (16,000 tokens cut 2 of 3 replies short, the thinking using most of it).
    stream = functools.partial(client.beta.messages.stream, betas=["server-side-fallback-2026-07-01"],
                               fallbacks="default")
    records = []
    for repeat in range(args.repeats):
        with stream(model=MODEL, max_tokens=64000, system=SYSTEM, thinking={"type": "adaptive"},
                    messages=[{"role": "user", "content": f"{notes}\n\n{prompt}"}]) as s:
            response = s.get_final_message()
        reply = "".join(b.text for b in response.content if b.type == "text")
        record = {"test": 32, "repeat": repeat, "reply": reply, "tool_calls": [], "executed": None,
                  "vault_changes": [], "model": response.model, "stop_reason": response.stop_reason,
                  "usage": {"input": response.usage.input_tokens, "output": response.usage.output_tokens}}
        record["score"], checks = score(record)
        record["checks"] = checks
        records.append(record)
        print(f"=== #{repeat} {response.model} score {record['score']} "
              f"missed {[label for label, ok in checks if not ok]}\n{reply}\n", flush=True)
    out = RUNS / f"reference-32-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"model": MODEL, "notes": NOTES, "prompt": prompt, "records": records}, indent=1))
    print(out)


if __name__ == "__main__":
    main()
