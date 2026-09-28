"""Eval 1 - thinking modes, on argument-only steps.

For each argument step (common.ARGUMENT_STEPS) and each thinking level - off, low, medium, high - the
model fills in the forced argument schema once, with `think` passed the documented way (top-level)
and, for off and low, the way Ollama's Granite page shows it (inside `options`). Temperature 0 and a
fixed seed.

Two levels are "the same" when Ollama renders the same prompt for them (identical prompt token
counts: a level the template reacts to adds its own tokens, as "low" does). Their outputs are not the
test: on this setup two identical requests can still diverge on a long generation, so `high` runs
twice and the second run is the noise baseline any other difference is measured against.

    uv run python nuru/evals/decision_layer/think_modes.py
"""

import json
import statistics

from common import ARGUMENT_STEPS, argument_messages, build_nuru_model, chat_once, save, tool_schemas

from core.forced_schema import arguments_schema, validate

CELLS = [("top", False), ("top", "low"), ("top", "medium"), ("top", "high"), ("top", "high#2"),
         ("options", False), ("options", "low")]
DETERMINISTIC = {"temperature": 0, "seed": 1}


def label(placement, think):
    return f"{placement}:{'off' if think is False else think}"


def think_value(think):
    return think.split("#")[0] if isinstance(think, str) else think


def main():
    model, base = build_nuru_model()
    schemas = tool_schemas()
    runs = []
    for step in ARGUMENT_STEPS:
        tool = schemas[step["tool"]]
        messages, known_ids = argument_messages(step, tool["description"])
        fmt = arguments_schema(step["tool"], tool["input_schema"], known_ids)
        for placement, think in CELLS:
            result = chat_once(model, messages, think_value(think), placement=placement, fmt=fmt, options=DETERMINISTIC)
            try:
                answer = json.loads(result["content"])
                valid, correct = not validate(answer, fmt), bool(step["check"](answer))
            except ValueError:
                valid = correct = False
            runs.append({"step": step["name"], "cell": label(placement, think), "valid": valid, "correct": correct,
                         **result})
            print(f"{step['name']:20} {label(placement, think):15} {result['seconds']:6.1f}s "
                  f"{result['completion_tokens']:5} tok  thinking {result['thinking_chars']:6} chars  "
                  f"valid={valid} correct={correct}", flush=True)

    def compare(a, b):
        pairs = [(r, s) for r in runs for s in runs if r["step"] == s["step"] and r["cell"] == a and s["cell"] == b]
        return {"same_prompt": all(r["prompt_tokens"] == s["prompt_tokens"] for r, s in pairs),
                "identical_outputs": sum(r["content"] == s["content"] and r["thinking"] == s["thinking"] for r, s in pairs),
                "of": len(pairs)}

    summary = {}
    for cell in dict.fromkeys(r["cell"] for r in runs):
        rows = [r for r in runs if r["cell"] == cell]
        summary[cell] = {
            "mean_seconds": round(statistics.mean(r["seconds"] for r in rows), 1),
            "mean_completion_tokens": round(statistics.mean(r["completion_tokens"] for r in rows)),
            "mean_thinking_chars": round(statistics.mean(r["thinking_chars"] for r in rows)),
            "empty_content": sum(not r["content"].strip() for r in rows),
            "valid": sum(r["valid"] for r in rows), "correct": sum(r["correct"] for r in rows), "of": len(rows),
        }
    identical = {
        "top:high vs top:high#2 (noise baseline)": compare("top:high", "top:high#2"),
        "top:medium vs top:high": compare("top:medium", "top:high"),
        "top:low vs top:high": compare("top:low", "top:high"),
        "top:off vs top:low": compare("top:off", "top:low"),
        "options:off vs top:high (options.think ignored?)": compare("options:off", "top:high"),
        "options:low vs top:high (options.think ignored?)": compare("options:low", "top:high"),
    }
    path = save("think_modes", {"model": model, "base_model": base, "summary": summary, "identical": identical,
                                "runs": runs})
    print("\ncell               secs  tokens  thinking  empty  valid  correct")
    for cell, s in summary.items():
        print(f"{cell:17} {s['mean_seconds']:6} {s['mean_completion_tokens']:7} {s['mean_thinking_chars']:9} "
              f"{s['empty_content']:6} {s['valid']:3}/{s['of']} {s['correct']:4}/{s['of']}")
    print("\ncomparison                                        same prompt  identical outputs")
    for claim, c in identical.items():
        print(f"{claim:50} {'yes' if c['same_prompt'] else 'no':11}  {c['identical_outputs']}/{c['of']}")
    print(f"\n{path}")


if __name__ == "__main__":
    main()
