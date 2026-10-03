"""Eval 2 - format enforcement in every thinking tier.

Each argument step, in each tier the config maps for this model (off / low / high), three times,
at Nuru's own sampling settings: does the output always parse and match the forced schema - above
all with thinking off, where Ollama once ignored `format` for qwen3.5 - and are the arguments right?

    uv run python nuru/evals/decision_layer/format_enforcement.py [--repeats 3]
"""

import argparse
import json

from common import ARGUMENT_STEPS, argument_messages, build_nuru_model, chat_once, save, tool_schemas

from core.forced_schema import arguments_schema, validate
from core.model_profile import LEVELS, for_model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    model, base = build_nuru_model()
    profile = for_model(base)
    tiers = {level: profile.think(level) for level in LEVELS}
    schemas = tool_schemas()
    runs = []
    for step in ARGUMENT_STEPS:
        tool = schemas[step["tool"]]
        messages, known_ids = argument_messages(step, tool["description"])
        fmt = arguments_schema(step["tool"], tool["input_schema"], known_ids)
        for tier, think in tiers.items():
            for repeat in range(args.repeats):
                result = chat_once(model, messages, think, fmt=fmt)
                parsed, problems, correct = False, ["empty content"], False
                if result["content"].strip():
                    try:
                        answer = json.loads(result["content"])
                        parsed, problems = True, validate(answer, fmt)
                        correct = not problems and bool(step["check"](answer))
                    except ValueError as e:
                        problems = [f"not JSON: {e}"]
                runs.append({"step": step["name"], "tier": tier, "repeat": repeat, "parsed": parsed,
                             "valid": parsed and not problems, "problems": problems, "correct": correct,
                             **{k: v for k, v in result.items() if k != "thinking"}})
                print(f"{step['name']:20} {tier:5} #{repeat} {result['seconds']:6.1f}s valid={parsed and not problems} "
                      f"correct={correct} {result['content'][:90]!r}", flush=True)

    summary = {
        tier: {
            "valid": sum(r["valid"] for r in runs if r["tier"] == tier),
            "correct": sum(r["correct"] for r in runs if r["tier"] == tier),
            "of": sum(r["tier"] == tier for r in runs),
            "empty": sum(not r["content"].strip() for r in runs if r["tier"] == tier),
            "seconds": round(sum(r["seconds"] for r in runs if r["tier"] == tier) / max(1, sum(r["tier"] == tier for r in runs)), 1),
        }
        for tier in tiers
    }
    path = save("format_enforcement", {"model": model, "base_model": base, "summary": summary, "runs": runs})
    print("\ntier   valid     correct   empty  mean secs")
    for tier, s in summary.items():
        print(f"{tier:5}  {s['valid']:3}/{s['of']}  {s['correct']:5}/{s['of']}  {s['empty']:5}  {s['seconds']:8}")
    print(f"\n{path}")


if __name__ == "__main__":
    main()
