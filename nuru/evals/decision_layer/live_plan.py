"""A plan written live for one turn by a planner model (nuru/modelfiles/planner/), in the planner
check's step language (evals/planner_check/plan.schema.json), turned into core.planner's format so
Session.run_plan can carry it out.

The planner is Nuru's own model with a planner Modelfile swapped in per request: the Modelfile's
SYSTEM goes as the system message (it replaces the model's own) and its PARAMETERs as options, with
the session's num_ctx. Same model name, same weights, same num_ctx - so Ollama uses the instance
that's already loaded instead of reloading (a different model name made Nuru's load check reload it).

Step languages:
- tool  {"id", "tool", "args"}         -> {"step", "tool", "with"}
- pick  {"id", "think": "pick", "from", "want"}
    -> pick (choose a note path) when a later tool step takes it whole as a path argument,
       else draft (extract a value, typed by the argument it fills - e.g. statistics' numbers)
- answer {"id", "think": "answer", "inputs"} -> {"step", "think": "answer", "uses", "want"}
A plan that is empty or breaks the language gives None: the turn is then Nuru's own (ask()).
"""

import json
import re
import time
from pathlib import Path

import jsonschema
import requests

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "planner_check" / "plan.schema.json").read_text())
OLLAMA = "http://localhost:11434"
_WHOLE_REF = re.compile(r"^\$(s[1-9][0-9]*)$")
# Arguments that take a note path: a pick feeding one of these chooses a path from the results.
PATH_ARGS = {"name", "note", "source_path", "destination_path", "file_paths", "supersedes", "targets"}
ANSWER_WANT = "a complete answer to my request"


def _refs(value) -> list[str]:
    """Step ids a value references whole ("$s2"), inside lists too."""
    if isinstance(value, str):
        m = _WHOLE_REF.match(value)
        return [m.group(1)] if m else []
    if isinstance(value, list):
        return [r for v in value for r in _refs(v)]
    return []


def problems(plan) -> list[str]:
    """Why `plan` can't be carried out as written - empty if it can. A missing final answer step
    is not a problem: run_plan adds one."""
    try:
        jsonschema.validate(plan, SCHEMA)
    except jsonschema.ValidationError as e:
        return [f"schema: {e.message[:200]}"]
    found, steps = [], plan["steps"]
    ids = [s["id"] for s in steps]
    if ids != [f"s{i}" for i in range(1, len(steps) + 1)]:
        found.append(f"ids not s1, s2, ... in order: {ids}")
    for i, s in enumerate(steps):
        if s.get("think") == "pick":
            used = [s["from"]]
        elif s.get("think") == "answer":
            used = s["inputs"]
            if i != len(steps) - 1:
                found.append(f"{s['id']}: answer before the last step")
        else:
            used = [r for v in s["args"].values() for r in _refs(v)]
            for k, v in s["args"].items():
                texts = v if isinstance(v, list) else [v]
                for t in texts:
                    if isinstance(t, str) and "$s" in t and not _WHOLE_REF.match(t):
                        found.append(f"{s['id']}: {k} has a reference inside text: {t!r}")
        for r in used:
            if r not in ids[:i]:
                found.append(f"{s['id']} uses {r}, not an earlier step")
    return found


def translate(plan: dict) -> dict:
    """The plan in core.planner's format (see the module docstring)."""
    steps = plan["steps"]
    feeds_path = {r for s in steps if "tool" in s for k, v in s["args"].items() if k in PATH_ARGS for r in _refs(v)}
    out = []
    for s in steps:
        if "tool" in s:
            out.append({"step": s["id"], "tool": s["tool"], "with": s["args"]})
        elif s["think"] == "pick":
            kind = "pick" if s["id"] in feeds_path else "draft"
            out.append({"step": s["id"], "think": kind, "uses": [s["from"]], "want": s["want"]})
        else:
            out.append({"step": s["id"], "think": "answer", "uses": s["inputs"], "want": ANSWER_WANT})
    return {"steps": out}


def modelfile_parts(path: Path) -> tuple[str, dict]:
    """A Modelfile's SYSTEM text and its PARAMETERs as Ollama options."""
    text = path.read_text()
    system = text.split('SYSTEM """', 1)[1].rsplit('"""', 1)[0]
    options = {k: float(v) if "." in v else int(v) for k, v in re.findall(r"^PARAMETER (\S+) (\S+)$", text, re.M)}
    return system, options


def make(prompt: str, modelfile: Path, model: str, num_ctx: int) -> dict:
    """Ask `model`, with the planner `modelfile` swapped in, for a plan for `prompt`. Returns what
    happened: the raw reply, the plan, any problems, the translated plan (None when the turn should
    be Nuru's own) and timings."""
    system, options = modelfile_parts(modelfile)
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "stream": False, "format": SCHEMA, "think": False, "options": {**options, "num_ctx": num_ctx}}
    start = time.monotonic()
    record = {"model": model, "modelfile": str(modelfile), "num_ctx": num_ctx}
    try:
        r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=300)
        r.raise_for_status()
        reply = r.json()
    except Exception as e:  # the turn goes on as Nuru's own
        record.update(seconds=round(time.monotonic() - start, 2), error=repr(e)[:300], plan=None, run=None,
                      fallback="planner call failed")
        return record
    record.update(seconds=round(time.monotonic() - start, 2), load_seconds=round(reply.get("load_duration", 0) / 1e9, 2),
                  prompt_tokens=reply.get("prompt_eval_count"), output_tokens=reply.get("eval_count"),
                  raw=reply.get("message", {}).get("content", ""))
    try:
        plan = json.loads(record["raw"])
    except json.JSONDecodeError:
        record.update(plan=None, run=None, fallback="not JSON")
        return record
    record["plan"] = plan
    found = problems(plan)
    record["problems"] = found
    if found:
        record.update(run=None, fallback="invalid plan")
    elif not plan["steps"]:
        record.update(run=None, fallback="empty plan")
    else:
        record.update(run=translate(plan), fallback=None)
    return record
