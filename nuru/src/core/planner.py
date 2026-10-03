"""Plan mode (a test): a message starting with "PLAN:" gets a plan of steps back, not an answer.

The plan is written in one model call with no tools offered and Ollama's `format` set to the plan's
JSON schema, so the model can only write a plan - it can't start doing the request. `check_plan`
then checks in code what the schema can't: references point back, args are complete, calculate gets
arithmetic. A plan it objects to goes back to the model once with the problems; the reply step is
the harness's own, added at the end. Nothing in a plan is run.
"""

import json
import re
from typing import Any

PREFIX = "PLAN:"
THINK_KINDS = ("pick", "draft")
_REF = re.compile(r"\$(s\d+)")
_FIELD_REF = re.compile(r"\$s\d+[.\[]")
_ARITHMETIC = re.compile(r"[0-9\s+\-*/^().]+")
REPAIR = "That plan has these problems:\n{problems}\nWrite the whole plan again with them fixed."

PLAN_SYSTEM = """You plan for an assistant that works in my Obsidian vault. You do not carry out the request and
you cannot see the vault: you write the list of steps that would carry it out, as JSON.

The plan is {{"steps": [...]}}. Each step is named "s1", "s2", ... and is one of:
- A tool step, run by the harness: {{"step": "s1", "tool": "<name>", "with": {{<its arguments>}}}}
- A think step, run by the assistant later, using the results of the steps it lists in "uses":
  - pick: choose one value from a tool's result, e.g. a note's path from search results.
    {{"step": "s2", "think": "pick", "uses": ["s1"], "want": "<what to choose>"}}
  - draft: write text a later step needs: note content, or a calculate expression built from numbers in a note.
    {{"step": "s4", "think": "draft", "uses": ["s3"], "want": "<what to write>"}}
Do not write the reply to me as a step: the harness adds it after your last step.
An argument can use "$sN", meaning the whole output of step sN, on its own or inside text ("Total: $s5").
There is no $sN.field or $sN[0]: to take one thing out of a result, pick it.

Tools (name(arguments) and what it does; arguments marked ? are optional):
{tools}

Rules:
- A step can only use steps before it.
- Never invent a path, id, title or number: search, then pick the note; read it; compute with calculate.
- Text I gave you in the request goes into args as a literal; draft only text that needs a result first.
- calculate takes plain arithmetic ("12.5 + 30"). When the numbers come from a note, draft the
  expression from the note first and pass "$sN".
- Today's daily note: write_note with type "daily" and no note. Use each tool's own argument names:
  read_note takes name, write_note takes note.

Example. Request: "Add Dana's new number, 555-0142, to her note."
{{"steps": [
  {{"step": "s1", "tool": "search", "with": {{"query": "Dana"}}}},
  {{"step": "s2", "think": "pick", "uses": ["s1"], "want": "Dana's person note"}},
  {{"step": "s3", "tool": "write_note", "with": {{"note": "$s2", "content": "- Phone: 555-0142", "mode": "append"}}}}
]}}"""


def is_plan_request(text: str) -> bool:
    return text.lstrip().upper().startswith(PREFIX)


def request_of(text: str) -> str:
    return text.lstrip()[len(PREFIX):].strip()


def _signature(tool: dict) -> str:
    fn = tool["function"]
    params = fn.get("parameters", {})
    required = set(params.get("required", []))
    args = ", ".join(name if name in required else f"{name}?" for name in params.get("properties", {}))
    description = " ".join(fn.get("description", "").split())
    return f"- {fn['name']}({args}): {description}"


def system_prompt(tools: list[dict]) -> str:
    return PLAN_SYSTEM.format(tools="\n".join(_signature(t) for t in tools))


def plan_schema(tool_names: list[str]) -> dict:
    """The `format` schema: a list of steps, each a tool step or a think step. Ollama sorts a
    schema's keys and makes the model write them in that order, so the key names are picked to
    sort into the order they read in: step, tool, with / step, think, uses, want."""
    tool_step = {
        "type": "object",
        "properties": {"step": {"type": "string"}, "tool": {"enum": tool_names}, "with": {"type": "object"}},
        "required": ["step", "tool", "with"],
        "additionalProperties": False,
    }
    think_step = {
        "type": "object",
        "properties": {
            "step": {"type": "string"},
            "think": {"enum": list(THINK_KINDS)},
            "uses": {"type": "array", "items": {"type": "string"}},
            "want": {"type": "string"},
        },
        "required": ["step", "think", "uses", "want"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"steps": {"type": "array", "items": {"anyOf": [tool_step, think_step]}, "minItems": 1}},
        "required": ["steps"],
        "additionalProperties": False,
    }


def check_plan(plan: Any, tools: list[dict]) -> list[str]:
    """What's wrong with `plan`, in words; empty when code finds nothing. The schema already holds
    the shape (when the grammar held); these are the checks it can't express."""
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list) or not plan["steps"]:
        return ["not a plan: no list of steps"]
    params = {t["function"]["name"]: t["function"].get("parameters", {}) for t in tools}
    problems = []
    seen: dict[str, dict] = {}

    def refs_back(step_id: str, ref: str, where: str) -> None:
        if ref not in seen:
            problems.append(f"{step_id}: {where} refers to {ref}, which is not an earlier step")

    steps = plan["steps"]
    for i, step in enumerate(steps):
        step_id = step.get("step", f"#{i + 1}")
        if step_id in seen:
            problems.append(f"{step_id}: the name is used twice")
        if "tool" in step:
            name = step["tool"]
            if name not in params:
                problems.append(f"{step_id}: {name} is not a tool")
            else:
                args = step.get("with") or {}
                known = params[name].get("properties", {})
                problems += [f"{step_id}: {name} has no argument {a}" for a in args if a not in known]
                problems += [f"{step_id}: {name} needs {a}" for a in params[name].get("required", []) if a not in args]
                for arg, value in args.items():
                    text = json.dumps(value)
                    for ref in _REF.findall(text):
                        refs_back(step_id, ref, arg)
                    if _FIELD_REF.search(text):
                        problems.append(f"{step_id}: {arg} takes part of a result with $sN.field or $sN[...]; pick it instead")
                expression = args.get("expression")
                if name == "calculate" and isinstance(expression, str) and not _REF.search(expression) \
                        and not _ARITHMETIC.fullmatch(expression):
                    problems.append(f"{step_id}: calculate's expression is not arithmetic; draft it from the note and pass $sN")
        elif step.get("think") in THINK_KINDS:
            kind = step["think"]
            uses = step.get("uses", [])
            for ref in uses:
                refs_back(step_id, ref, "uses")
            if kind == "pick" and not any(ref in seen and "tool" in seen[ref] for ref in uses):
                problems.append(f"{step_id}: pick uses no tool result to pick from")
        else:
            problems.append(f"{step_id}: neither a tool step nor a think step")
        seen[step_id] = step
    return problems


def make_plan(request: str, tools: list[dict], chat_json, think, repairs: int = 1) -> tuple[Any, list[str]]:
    """Plan `request`: `chat_json(messages, schema, think)` returns the reply text. A plan check_plan
    objects to goes back to the model with its problems, up to `repairs` times. Returns the plan,
    with the harness's answer step added (None if the reply wasn't JSON), and the problems left."""
    messages = [{"role": "system", "content": system_prompt(tools)}, {"role": "user", "content": request}]
    schema = plan_schema([t["function"]["name"] for t in tools])
    for attempt in range(repairs + 1):
        raw = chat_json(messages, schema, think)
        try:
            plan = json.loads(raw)
        except json.JSONDecodeError:
            return None, [f"not JSON: {raw[:200]!r}"]
        problems = check_plan(plan, tools)
        if not problems or attempt == repairs:
            break
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": REPAIR.format(problems="\n".join(f"- {p}" for p in problems))}]
    names = [step.get("step") for step in plan["steps"]]
    plan["steps"].append({"step": f"s{len(names) + 1}", "think": "answer", "uses": names, "want": "the reply to the request"})
    return plan, problems
