"""Eval 8 - sorting prompts into categories: Laya against the 3B, several labels per prompt.

Every prompt in prompt_categories.py goes to both, with the same seven category definitions:

- Laya is asked up to 3 times; each time the category it picked is removed from the options, so
  its picks come out most likely first. Its time is the whole loop's.
- Laya is also asked once with seven yes/no questions, one per category ("handling the request
  needs this: <definition>"); each category it gives over 0.5 is a label, and the likeliest one if
  none is. Its picks are ordered most likely first.
- The 3B (granite4.2:3b, no Nuru system prompt or tools) is asked once for a list of 1-3
  categories, most important first, in a forced JSON schema. Its time is that call's.

Laya runs first, then the 3B, so they never share the GPU. Each warms up untimed first. Laya's
picks are scored after 1, 2 and 3 calls; its first call's probabilities are kept too, to see what
a single call's top 2 or 3 would have scored (in that one call's time). See categorisation-notes.md.

    caffeinate -i uv run python categorise.py [--think low|off|high] [--only laya|3b]
                                              [--laya-checkpoint .runs/laya-categorise-<time>]

`--set user` runs the user's 100 prompts (prompt_categories_user.py) and `--set third` their second
100 (prompt_categories_third.py) instead of the first 50, and report each set's GROUPS separately.

`--laya-checkpoint` asks a checkpoint folder (a fine-tuned one, from train_categorise.py) through
`laya.Agent` directly, instead of the pinned checkpoints Nuru's Laya server loads.
"""

import argparse
import json
import os
import statistics
import time
from types import SimpleNamespace

from common import chat_once, save
import prompt_categories_third
import prompt_categories_user
from prompt_categories import CATEGORIES, PROMPTS

from laya_mcp.config import load_config
from laya_mcp.predictor import LayaPredictor
from laya_mcp.question_sets import QuestionSet

MODEL = "granite4.2:3b"
MAX_PICKS = 3
INSTRUCTIONS = ("Which kind of work does the user's `request` need from an assistant that can read and "
                "write the user's notes and ask an expert?")
SYSTEM = (
    "You sort requests sent to Nuru, an assistant that can read and write the user's Obsidian notes and "
    "ask an expert model. Don't answer the request. Give every category it needs, most important first, "
    f"1 to {MAX_PICKS} of them.\n\nCategories:\n" + "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())
)
SCHEMA = {
    "type": "object",
    "properties": {"categories": {"type": "array", "items": {"type": "string", "enum": list(CATEGORIES)},
                                  "minItems": 1, "maxItems": MAX_PICKS}},
    "required": ["categories"],
}


def question_set(options: dict[str, str]) -> QuestionSet:
    return QuestionSet.model_validate({
        "name": "categorise", "version": 1, "checkpoint": "english", "max_state_tokens": 400,
        "questions": {"category": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": options}},
    })


YES_NO = QuestionSet.model_validate({
    "name": "categorise_yes_no", "version": 1, "checkpoint": "english", "max_state_tokens": 400,
    "questions": {k: {"type": "noul", "instructions": f"Handling the user's `request` needs this: {v}."}
                  for k, v in CATEGORIES.items()},
})


class CheckpointPredictor:
    """A checkpoint folder behind the same `predict` call as LayaPredictor."""

    def __init__(self, path: str):
        from laya import Agent

        self.agent = Agent(os.path.abspath(path))

    def predict(self, qset: QuestionSet, state, questions: dict):
        return SimpleNamespace(answers=self.agent.predict(state, questions)["answers"])


def ask_laya_yes_no(predictor: LayaPredictor, prompt: str) -> dict:
    started = time.perf_counter()
    answers = predictor.predict(YES_NO, {"request": prompt}, YES_NO.render()).answers
    seconds = round(time.perf_counter() - started, 3)
    p = {k: round(answers[k]["noul"], 3) for k in CATEGORIES}
    ranked = sorted(p, key=p.get, reverse=True)
    return {"seconds": seconds, "picks": [k for k in ranked if p[k] > 0.5] or ranked[:1], "p": p}


def ask_laya(predictor: LayaPredictor, prompt: str) -> dict:
    remaining, picks, first_probabilities = dict(CATEGORIES), [], None
    started = time.perf_counter()
    for _ in range(MAX_PICKS):
        qset = question_set(remaining)
        answer = predictor.predict(qset, {"request": prompt}, qset.render()).answers["category"]
        first_probabilities = first_probabilities or answer["probabilities"]
        picks.append({"category": answer["choice"], "p": round(answer["probabilities"][answer["choice"]], 3),
                      "seconds": round(time.perf_counter() - started, 3)})  # since the loop began
        del remaining[answer["choice"]]
    return {"seconds": round(time.perf_counter() - started, 3), "picks": picks,
            "first_probabilities": {k: round(v, 3) for k, v in first_probabilities.items()}}


def ask_model(prompt: str, think) -> dict:
    call = chat_once(MODEL, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                     think, fmt=SCHEMA)
    try:
        raw = json.loads(call["content"])["categories"]
    except (ValueError, KeyError, TypeError):
        raw = []
    picks = list(dict.fromkeys(c for c in raw if c in CATEGORIES))[:MAX_PICKS]
    return {"seconds": call["seconds"], "picks": picks, "raw": call["content"],
            "completion_tokens": call["completion_tokens"], "thinking_chars": call["thinking_chars"]}


def score(predicted: list[str], gold: set[str]) -> dict:
    hits = len(set(predicted) & gold)
    return {"exact": set(predicted) == gold, "first_right": bool(predicted) and predicted[0] in gold,
            "recall": hits / len(gold), "precision": hits / len(predicted) if predicted else 0.0}


def summary(name: str, scores: list[dict], seconds: list[float]) -> dict:
    n = len(scores)
    s = {"exact": sum(x["exact"] for x in scores), "first_right": sum(x["first_right"] for x in scores),
         "recall": round(sum(x["recall"] for x in scores) / n, 3),
         "precision": round(sum(x["precision"] for x in scores) / n, 3)}
    if seconds:
        s |= {"median_s": round(statistics.median(seconds), 3), "total_s": round(sum(seconds), 1)}
    print(f"  {name:18} exact {s['exact']:2}/{n}  first right {s['first_right']:2}/{n}  "
          f"recall {s['recall']:.2f}  precision {s['precision']:.2f}"
          + (f"  median {s['median_s']:.2f}s  total {s['total_s']}s" if seconds else ""))
    return s


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--think", default="low", help="the 3B's thinking: off, low or high")
    parser.add_argument("--only", choices=["laya", "3b"])
    parser.add_argument("--set", choices=["first", "user", "third"], default="first", help="which test prompts")
    parser.add_argument("--laya-checkpoint", help="a checkpoint folder to ask instead of the pinned one")
    args = parser.parse_args()
    think = False if args.think == "off" else args.think
    module = {"user": prompt_categories_user, "third": prompt_categories_third}.get(args.set)
    prompts, groups = (module.PROMPTS, module.GROUPS) if module else (PROMPTS, {})
    rows = [{"prompt": p, "gold": sorted(g)} for p, g in prompts]

    if args.only != "3b":
        if args.laya_checkpoint:
            predictor = CheckpointPredictor(args.laya_checkpoint)
        else:
            predictor = LayaPredictor(load_config().laya)
            predictor.start()
            while predictor.status()["state"] == "loading":
                time.sleep(0.2)
            if predictor.status()["state"] != "ready":
                raise SystemExit(f"Laya failed to load: {predictor.status()['error']}")
        for _ in range(3):
            ask_laya(predictor, "Warm-up request.")
            ask_laya_yes_no(predictor, "Warm-up request.")
        for row in rows:
            row["laya"] = ask_laya(predictor, row["prompt"])
            row["laya_yes_no"] = ask_laya_yes_no(predictor, row["prompt"])
            print(f"laya {row['laya']['seconds']:6.3f}s  {[p['category'] for p in row['laya']['picks']]}  "
                  f"yes/no {row['laya_yes_no']['seconds']:6.3f}s  {row['laya_yes_no']['picks']}  "
                  f"{row['gold']}  {row['prompt'][:60]}", flush=True)

    if args.only != "laya":
        ask_model("Warm-up request.", think)
        for row in rows:
            row["3b"] = ask_model(row["prompt"], think)
            print(f"3b   {row['3b']['seconds']:6.2f}s  {row['3b']['picks']}  {row['gold']}  "
                  f"{row['prompt'][:60]}", flush=True)

    results = {}
    several = [r for r in rows if len(r["gold"]) > 1]
    subsets = [(f"all {len(rows)}", rows), (f"several labels {len(several)}", several)]
    subsets += [(f"{name} {len(members)}", [r for r in rows if r["prompt"] in members])
                for name, members in groups.items() if members]
    if groups:
        grouped = set().union(*groups.values())
        rest = [r for r in rows if r["prompt"] not in grouped]
        subsets.append((f"none of the groups {len(rest)}", rest))
    for subset, chosen in subsets:
        print(f"\n{subset}:")
        results[subset] = {}
        if "laya" in rows[0]:
            for k in range(1, MAX_PICKS + 1):
                results[subset][f"laya_loop_{k}"] = summary(
                    f"Laya, {k} call{'s' * (k > 1)}",
                    [score([p["category"] for p in r["laya"]["picks"][:k]], set(r["gold"])) for r in chosen],
                    [r["laya"]["picks"][k - 1]["seconds"] for r in chosen])
            for k in (2, 3):
                results[subset][f"laya_one_call_top{k}"] = summary(
                    f"Laya, 1 call top {k}",
                    [score(sorted(r["laya"]["first_probabilities"], key=r["laya"]["first_probabilities"].get,
                                  reverse=True)[:k], set(r["gold"])) for r in chosen],
                    [r["laya"]["picks"][0]["seconds"] for r in chosen])
            results[subset]["laya_yes_no"] = summary(
                "Laya, yes/no", [score(r["laya_yes_no"]["picks"], set(r["gold"])) for r in chosen],
                [r["laya_yes_no"]["seconds"] for r in chosen])
        if "3b" in rows[0]:
            results[subset]["3b"] = summary(f"3B (think {args.think})",
                                            [score(r["3b"]["picks"], set(r["gold"])) for r in chosen],
                                            [r["3b"]["seconds"] for r in chosen])
    print(save("categorise", {"think": args.think, "model": MODEL, "set": args.set,
                              "laya_checkpoint": args.laya_checkpoint, "summary": results, "rows": rows}))


if __name__ == "__main__":
    main()
