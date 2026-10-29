"""A local model's tool call in the tools API's form, every time (TODO F29).

Some models write a call into their reply instead of making it: `search("car service")`,
`search{query: "car service"}`, `calculate with expression="2 * 3"` (nemotron-3.5-bolt copies whatever
form its system prompt shows), or make one Ollama's parser can't read and drops, leaving a reply
with tokens in it and nothing to show. Session.ask watches for both. A reply that starts like a call
is held back from the user (`may_be_call`), and if it is one (`looks_like_call`), or the model wrote
tokens and nothing came of them, the same step is asked again under Ollama's `format`: `step_schema`
allows one offered tool with arguments that fit its schema, or an answer, so the model can only
emit one of the two, and `read_step` takes the one it chose.

What this can't do is choose for the model: it guarantees the call's form, not that the call is the
right one.
"""

import copy
import json
import re

import jsonschema

# Arguments a call can't do without although the tool's schema leaves them optional (it takes
# several shapes of call): without them a forced call could be a well-formed empty one.
REQUIRED = {"write_note": ("content",)}
# How much of a reply is held back while it might still be a call written as text; one that is
# still undecided past this is shown. A call written as text is one short line.
HOLD_CHARS = 200
_ANSWER = {"type": "object", "properties": {"answer": {"type": "string", "minLength": 1}},
           "required": ["answer"], "additionalProperties": False}
_LEAD = r"\s*(?:`+\s*|action:\s*|call\s+|<\|tool_call>\s*|call:\s*)*"


def _names(tools: list[dict]) -> str:
    return "|".join(sorted(re.escape(t["function"]["name"]) for t in tools))


def looks_like_call(text: str, tools: list[dict]) -> bool:
    """Whether `text` starts with a call of one of `tools` written out: name(, name{, "name with",
    or a JSON object naming it, after the labels and markup models put before one."""
    if not tools:
        return False
    names = _names(tools)
    return bool(re.match(rf"{_LEAD}(?:{names})\s*(?:\(|\{{| with\b)", text, re.IGNORECASE)
                or re.match(rf"{_LEAD}\{{\s*\"(?:name|tool|function)\"\s*:\s*\"(?:{names})\"", text, re.IGNORECASE))


def may_be_call(text: str, tools: list[dict]) -> bool:
    """Whether a reply that has so far streamed `text` could still turn out to be a call written as
    text: then it isn't shown yet. False once it is clearly something else."""
    if looks_like_call(text, tools):
        return True
    if not tools or len(text) > HOLD_CHARS or "\n" in text.strip():
        return False
    rest = re.sub(rf"^{_LEAD}", "", text, flags=re.IGNORECASE)
    if not rest or rest.startswith("{"):
        return True  # nothing but labels yet, or what may be a call as JSON
    word = re.match(r"\w*", rest).group(0).lower()
    names = [t["function"]["name"] for t in tools]
    if rest.lower() == word:
        return any(name.startswith(word) for name in names)  # a name still being written
    return word in names and bool(re.fullmatch(r"\s*(?:w(?:i(?:th?)?)?)?", rest[len(word):], re.IGNORECASE))


def step_schema(tools: list[dict]) -> dict:
    """The `format` schema for one step: {"tool": <name>, "arguments": {...}} for one of `tools`,
    with arguments that fit its own schema (no other fields), or {"answer": "..."}."""
    branches, definitions = [], {}
    for tool in tools:
        function = tool["function"]
        arguments = copy.deepcopy(function.get("parameters") or {"type": "object"})
        definitions.update(arguments.pop("$defs", {}))  # refs are "#/$defs/...": from the root
        arguments.pop("title", None)
        arguments.setdefault("type", "object")
        arguments.setdefault("properties", {})
        arguments["additionalProperties"] = False
        required = list(arguments.get("required", []))
        required += [name for name in REQUIRED.get(function["name"], ()) if name in arguments["properties"] and name not in required]
        if required:
            arguments["required"] = required
        branches.append({"type": "object", "properties": {"tool": {"const": function["name"]}, "arguments": arguments},
                         "required": ["tool", "arguments"], "additionalProperties": False})
    schema = {"anyOf": [*branches, _ANSWER]}
    if definitions:
        schema["$defs"] = definitions
    return schema


def read_step(text: str, schema: dict) -> dict | None:
    """What a reply under `schema` chose: {"tool_call": <a tools-API call>} or {"answer": text};
    None if it doesn't fit (the grammar should rule that out, and this checks it in code)."""
    try:
        step = json.loads(text)
        jsonschema.validate(step, schema)
    except (ValueError, jsonschema.ValidationError):
        return None
    if "answer" in step:
        return {"answer": step["answer"]}
    return {"tool_call": {"function": {"name": step["tool"], "arguments": step["arguments"]}}}
