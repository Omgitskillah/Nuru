"""JSON schemas that force a tool call's arguments, for Ollama's `format`.

Ollama drops `tool_choice`, so a tool call can't be forced through the tools API; a schema passed
as `format` can, because the model can then only emit JSON that matches it. The schema is built
fresh for each call from the tool's own input schema, with two changes:

- every note-ID field is an enum of the IDs the last search actually returned, so the model can't
  invent one (and a required ID with nothing to choose from leaves only the way out);
- a way out, `{"cannot_do": "<reason>"}`, as the other branch of an `anyOf`. Ollama's grammar
  conversion handles `anyOf` (measured on granite4.2:3b, 2026-09-28, in every thinking mode).

`validate()` checks an answer against a schema this module built, in code: the grammar should
already guarantee it, and this is how that guarantee gets tested rather than assumed.
"""

import copy
from typing import Any

# Tool arguments that name a note by ID. Tool knowledge, not model knowledge.
NOTE_ID_FIELDS = {"write_note": ("supersedes", "targets")}

CANNOT_DO = {
    "type": "object",
    "properties": {"cannot_do": {"type": "string"}},
    "required": ["cannot_do"],
    "additionalProperties": False,
}


def _id_enum(known_ids: list[str]) -> dict:
    return {"type": "string", "enum": list(known_ids)}


def arguments_schema(tool: str, input_schema: dict, known_ids: list[str]) -> dict:
    """The `format` schema for one call of `tool`: its arguments or a cannot_do."""
    arguments = copy.deepcopy(input_schema)
    arguments.pop("title", None)
    properties = arguments.get("properties", {})
    required = set(arguments.get("required", []))
    for field in NOTE_ID_FIELDS.get(tool, ()):
        if field not in properties:
            continue
        if not known_ids:
            if field in required:
                return {"anyOf": [CANNOT_DO]}
            del properties[field]
            continue
        is_list = "array" in _types(properties[field])
        ids = {"type": "array", "items": _id_enum(known_ids)} if is_list else _id_enum(known_ids)
        properties[field] = ids if field in required else {"anyOf": [ids, {"type": "null"}]}
    for spec in properties.values():
        spec.pop("title", None)
    arguments["additionalProperties"] = False
    return {"anyOf": [arguments, CANNOT_DO]}


def _types(spec: dict) -> set[str]:
    if "anyOf" in spec:
        return set().union(*(_types(s) for s in spec["anyOf"]))
    kind = spec.get("type")
    return set(kind) if isinstance(kind, list) else {kind} if kind else set()


_JSON_TYPES = {
    "string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict,
    "null": type(None),
}


def validate(value: Any, schema: dict) -> list[str]:
    """Problems with `value` against a schema of the subset this module builds; empty if none."""
    if "anyOf" in schema:
        branches = [validate(value, s) for s in schema["anyOf"]]
        return [] if any(not b for b in branches) else [f"matches no branch: {branches}"]
    problems = []
    kind = schema.get("type")
    if kind:
        expected = _JSON_TYPES[kind]
        if not isinstance(value, expected) or (kind in ("integer", "number") and isinstance(value, bool)):
            return [f"expected {kind}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{value!r} is not one of {schema['enum']}")
    if kind == "array" and "items" in schema:
        for i, item in enumerate(value):
            problems += [f"[{i}] {p}" for p in validate(item, schema["items"])]
    if kind == "object":
        properties = schema.get("properties", {})
        for field in schema.get("required", []):
            if field not in value:
                problems.append(f"missing {field}")
        for field, item in value.items():
            if field in properties:
                problems += [f"{field}: {p}" for p in validate(item, properties[field])]
            elif schema.get("additionalProperties") is False:
                problems.append(f"unexpected field {field}")
    return problems
