"""A local model's tool call in the tools API's form, every time (TODO F29).

Some models write a call into their reply instead of making it: `search("car service")`,
`search{query: "car service"}`, `calculate with expression="2 * 3"` (nemotron-3.5-bolt copies whatever
form its system prompt shows), or make one Ollama's parser can't read and drops, leaving a reply
with tokens in it and nothing to show. Session.ask watches for both (`trigger`). A reply that starts
like a call is held back from the user (`may_be_call`); if it turns out to be one, or nothing came
of the tokens, the same step is asked again once under Ollama's `format`: `step_schema` allows one
offered tool with arguments that fit its schema, or an answer. `read_step` (and `native_step`, for
a native call made anyway) takes what the model chose, and refuses an answer that is blank or is
itself just a call written out: then the turn ends saying so, rather than show a call as a reply.

A prefix can't tell an attempted call from an explanation that starts with one, so asking again
lets the model answer instead; only an answer that is nothing but a call (`bare_call`) is refused.
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
# How much of a reply is held back while it is undecided: the start of a tool's name, a "{", labels.
# One that already reads as a call written out (looks_like_call) is held to its end, however long,
# since it is either asked again or ends the turn. A call written as text is one short line.
HOLD_CHARS = 200
TEXT_CALL, EMPTY_REPLY = "text_call", "empty_reply"
_ANSWER = {"type": "object", "properties": {"answer": {"type": "string", "minLength": 1}},
           "required": ["answer"], "additionalProperties": False}
_LEAD = r"\s*(?:`+(?:json)?\s*|action:\s*|call\s+|<\|tool_call>\s*|call:\s*)*"


def _names(tools: list[dict]) -> str:
    return "|".join(sorted(re.escape(t["function"]["name"]) for t in tools))


def _call_start(text: str, tools: list[dict]) -> re.Match | None:
    names = _names(tools)
    return (re.match(rf"{_LEAD}(?:{names})\s*(?:\(|\{{| with\b)", text, re.IGNORECASE)
            or re.match(rf"{_LEAD}\{{\s*\"(?:name|tool|function)\"\s*:\s*\"(?:{names})\"", text, re.IGNORECASE))


def looks_like_call(text: str, tools: list[dict]) -> bool:
    """Whether `text` starts with a call of one of `tools` written out: name(, name{, "name with",
    or a JSON object naming it, after the labels and markup models put before one."""
    return bool(tools) and _call_start(text, tools) is not None


def trigger(text: str, tools: list[dict], thinking: str, completion_tokens: int) -> str | None:
    """Why a reply with no native call should be asked again: TEXT_CALL, a call written out;
    EMPTY_REPLY, tokens written and nothing to show for them (suspected: Ollama's parser dropped a
    call it couldn't read; a model that only reasoned is the silent-turn retry's); None if neither."""
    if looks_like_call(text, tools):
        return TEXT_CALL
    if not text.strip() and not thinking and completion_tokens > 0:
        return EMPTY_REPLY
    return None


def bare_call(text: str, tools: list[dict]) -> bool:
    """Whether `text` is nothing but a call written out (and the result a model sometimes writes
    after one, "-> 409.5"), rather than words that start with one."""
    start = _call_start(text, tools) if tools else None
    if start is None:
        return False
    rest = text[start.end():]
    body = text[re.match(_LEAD, text, re.IGNORECASE).end():]
    if body.startswith("{"):  # a JSON call: where the object ends, however it's laid out
        try:
            rest = body[json.JSONDecoder().raw_decode(body)[1]:]
        except ValueError:
            return True  # never closed: a cut-off call
    elif start.group(0).rstrip().endswith(("(", "{")):  # find where the brackets close
        depth, quote, i = 1, None, 0
        while i < len(rest) and depth:
            c = rest[i]
            if quote:
                if c == "\\":
                    i += 1
                elif c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            i += 1
        if depth:
            return True  # never closed: a cut-off call
        rest = rest[i:]
    else:  # "name with ...": one line of it
        return "\n" not in text.strip()
    rest = rest.strip().strip("`").strip()
    return not rest or rest.startswith("->")


def may_be_call(text: str, tools: list[dict]) -> bool:
    """Whether a reply that has so far streamed `text` could still turn out to be a call written as
    text: then it isn't shown yet. False once it is clearly something else (see HOLD_CHARS)."""
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


def _checked(step, schema: dict, tools: list[dict]) -> dict | None:
    try:
        jsonschema.validate(step, schema)
    except jsonschema.ValidationError:
        return None
    if "answer" in step:
        answer = step["answer"]
        return None if not answer.strip() or bare_call(answer, tools) else {"answer": answer}
    return {"tool_call": {"function": {"name": step["tool"], "arguments": step["arguments"]}}}


def read_step(text: str, schema: dict, tools: list[dict]) -> dict | None:
    """What a reply under `schema` chose: {"tool_call": <a tools-API call>} or {"answer": text}.
    None if it doesn't fit (the grammar should rule that out, and this checks it in code), or if
    the answer is blank or nothing but a call written out again."""
    try:
        step = json.loads(text)
    except ValueError:
        return None
    return _checked(step, schema, tools)


def native_step(call: dict, schema: dict, tools: list[dict]) -> dict | None:
    """A native call made in reply to the forced step, if its arguments fit `schema`."""
    function = call.get("function") or {}
    return _checked({"tool": function.get("name"), "arguments": function.get("arguments")}, schema, tools)
