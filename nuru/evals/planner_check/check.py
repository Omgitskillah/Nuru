"""Score planner outputs against the reference plans (5 criteria, 1 point each).

Usage: python3 check.py RESULTS.jsonl SCORES.json
Structure and dependencies are fully mechanical. Tool choice, arguments and fidelity are mechanical
where the plan matches the reference after normalisation; anything else is marked "review" with
the provisional point withheld, so every non-exact plan gets a human look.
"""
import json
import re
import sys
from pathlib import Path

import jsonschema

HERE = Path(__file__).parent
SCHEMA = json.loads((HERE / "plan.schema.json").read_text())
CONTRACTS = json.loads((HERE / "tool-contracts.json").read_text())
REF = re.compile(r"^\$s([1-9][0-9]*)$")
# Free-text arguments whose wording may differ from the reference (paraphrase allowed).
FREE = {("search", "query"), ("ask_an_expert", "question"), ("write_note", "content")}


def kind(step):
    return step.get("tool") or step.get("think")


def norm_args(tool, args):
    """Drop arguments equal to their declared default; normalise expression spacing."""
    props = CONTRACTS[tool]["properties"]
    out = {}
    for k, v in args.items():
        if k in props and "default" in props[k] and v == props[k]["default"]:
            continue
        if tool == "calculate" and k == "expression" and isinstance(v, str):
            v = v.replace(" ", "")
        out[k] = v
    return out


def refs_of(step):
    """Ids a step depends on."""
    if step.get("think") == "pick":
        return [step.get("from")]
    if step.get("think") == "answer":
        return list(step.get("inputs", []))
    found = []

    def walk(v):
        if isinstance(v, str) and REF.match(v):
            found.append("s" + REF.match(v).group(1))
        elif isinstance(v, list):
            for x in v:
                walk(x)
    for v in step.get("args", {}).values():
        walk(v)
    return found


def dependencies_ok(steps):
    problems = []
    ids = [s.get("id") for s in steps]
    if ids != [f"s{i}" for i in range(1, len(steps) + 1)]:
        problems.append(f"ids not sequential: {ids}")
    for i, s in enumerate(steps):
        for r in refs_of(s):
            if r not in ids[:i]:
                problems.append(f"{s.get('id')} references {r}, not an earlier step")
        # partial references like "$s2.name" or "read $s2" inside a string
        for v in json.dumps(s.get("args", {})).split('"'):
            if "$s" in v and not REF.match(v):
                problems.append(f"{s.get('id')} has a non-whole reference {v!r}")
    answers = [i for i, s in enumerate(steps) if s.get("think") == "answer"]
    if len(answers) != 1 or answers[0] != len(steps) - 1:
        problems.append("not exactly one answer step, last")
    return problems


def align(model_steps, ref_steps):
    """Map model step ids to reference ids when the step kinds match one to one (independent
    reads may be reordered). Returns a dict or None."""
    if len(model_steps) != len(ref_steps):
        return None
    if [kind(s) for s in model_steps] == [kind(s) for s in ref_steps]:
        return {m["id"]: r["id"] for m, r in zip(model_steps, ref_steps)}
    if sorted(kind(s) for s in model_steps) == sorted(kind(s) for s in ref_steps):
        return "reordered"
    return None


def remap(v, mapping):
    if isinstance(v, str) and REF.match(v):
        return "$" + mapping.get(v[1:], v[1:])
    if isinstance(v, list):
        return [remap(x, mapping) for x in v]
    return v


def score(content, tool_calls, ref):
    s = {"structure": 0, "tool_choice": 0, "arguments": 0, "dependencies": 0, "fidelity": 0}
    notes, review = [], []
    try:
        plan = json.loads(content)
        jsonschema.validate(plan, SCHEMA)
        if tool_calls:
            raise ValueError("native tool calls")
        s["structure"] = 1
    except Exception as e:
        notes.append(f"structure: {str(e).splitlines()[0][:160]}")
        try:
            plan = json.loads(content)
        except Exception:
            return s, notes, review, None
    steps = plan.get("steps", []) if isinstance(plan, dict) else []
    rsteps = ref["steps"]

    if not rsteps:
        if not steps:
            return {k: 1 for k in s} if s["structure"] else s, notes, review, plan
        notes.append("expected an empty plan")
        return {k: 0 for k in s}, notes, review, plan
    if not steps:
        notes.append("empty plan where a plan was expected")
        return s, notes, review, plan

    dep = dependencies_ok(steps)
    notes += [f"dependencies: {p}" for p in dep]

    m_tools = sorted(st.get("tool") for st in steps if "tool" in st)
    r_tools = sorted(st.get("tool") for st in rsteps if "tool" in st)
    s["tool_choice"] = int(m_tools == r_tools)
    if m_tools != r_tools:
        notes.append(f"tools {m_tools} vs reference {r_tools}")

    mapping = align(steps, rsteps)
    args_ok, args_review = True, False
    if isinstance(mapping, dict):
        for m, r in zip(steps, rsteps):
            if "tool" in m:
                ma = {k: remap(v, mapping) for k, v in norm_args(m["tool"], m.get("args", {})).items()}
                ra = norm_args(r["tool"], r["args"])
                if set(ma) != set(ra):
                    args_ok = False
                    notes.append(f"{m['id']} {m['tool']} args {ma} vs reference {ra}")
                    continue
                for k in ra:
                    if ma[k] == ra[k]:
                        continue
                    if (m["tool"], k) in FREE:
                        args_review = True
                        review.append(f"{m['id']} {m['tool']}.{k}: {ma[k]!r} vs reference {ra[k]!r}")
                    else:
                        args_ok = False
                        notes.append(f"{m['id']} {m['tool']}.{k}={ma[k]!r} vs reference {ra[k]!r}")
            elif m.get("think") == "pick":
                if mapping.get(m.get("from")) != r.get("from"):
                    notes.append(f"{m['id']} picks from {m.get('from')}, reference from {r.get('from')}")
                    args_ok = False
                review.append(f"{m['id']} pick want: {m.get('want')!r}")
                args_review = True
            elif m.get("think") == "answer":
                if sorted(mapping.get(x, x) for x in m.get("inputs", [])) != sorted(r["inputs"]):
                    dep.append("answer inputs differ")
                    notes.append(f"answer inputs {m.get('inputs')} vs reference {r['inputs']}")
    else:
        # Shape differs: arguments still count as correct if every tool step's arguments equal those of a
        # same-tool reference step (free text may be paraphrased, step references are not compared).
        def comparable(tool, args):
            return {k: (None if (tool, k) in FREE or (isinstance(v, str) and REF.match(v)) else v)
                    for k, v in norm_args(tool, args).items()}
        ref_args = [(r["tool"], comparable(r["tool"], r["args"])) for r in rsteps if "tool" in r]
        model_args = [(m["tool"], comparable(m["tool"], m.get("args", {}))) for m in steps if "tool" in m]
        args_ok = bool(model_args) and all(a in ref_args for a in model_args)
        if not args_ok:
            notes.append("arguments: " + "; ".join(f"{t}{a}" for t, a in model_args if (t, a) not in ref_args))
        review.append("shape differs from reference")
    s["dependencies"] = int(not dep)
    s["arguments"] = int(args_ok)
    exact = s["structure"] and s["tool_choice"] and args_ok and not dep and isinstance(mapping, dict)
    s["fidelity"] = int(bool(exact))
    if args_review and exact:
        review.append("free-text fields differ: confirm paraphrase and the pick wants")
    return s, notes, review, plan


def main():
    results = [json.loads(l) for l in Path(sys.argv[1]).read_text().splitlines()]
    cases = {c["id"]: c for f in ("cases.json", "cases_yesterday.json")
             for c in json.loads((HERE / f).read_text())}
    scored = []
    for r in results:
        if r.get("warmup"):
            continue
        case = cases[r["case"]]
        # Best match over the reference and any accepted alternative plans.
        s, notes, review, plan = max((score(r.get("content") or "", r.get("tool_calls"), ref)
                                      for ref in [case["reference"]] + case.get("alternatives", [])),
                                     key=lambda t: sum(t[0].values()))
        scored.append({**{k: r[k] for k in ("model", "case", "repeat", "wall_s", "done_reason",
                                            "prompt_tokens", "output_tokens")},
                       "scores": s, "total": sum(s.values()), "notes": notes, "review": review, "plan": plan,
                       "raw": None if plan is not None else r.get("content")})
    Path(sys.argv[2]).write_text(json.dumps(scored, indent=1))


if __name__ == "__main__":
    main()
