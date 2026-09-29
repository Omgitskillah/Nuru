"""Write arguments under enforcement: the model's own arguments are validated in code (pydantic, one
model per write tool; note IDs only from what the last search returned). If they fail, the harness
asks for that tool's arguments again with Ollama's `format` set to a schema built for this call
(core.forced_schema) - the same ID limit, with a `cannot_do` way out - and validates that answer
the same way, before gate_write sees it.

The grammar should make invalid output impossible; validation is how that is checked rather than
trusted, and what catches a model that satisfies the grammar with the wrong kind of value.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.forced_schema import NOTE_ID_FIELDS, arguments_schema


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppendNote(_Args):
    id: str
    content: str = Field(min_length=1)


class AddToDaily(_Args):
    text: str = Field(min_length=1)


class CreateNote(_Args):
    type: str
    title: str = Field(min_length=1)
    author: str
    body: str = ""
    tags: list[str] | None = None
    supersedes: str | None = None
    date: str | None = None
    targets: list[str] | None = None
    session: str | None = None


class ArchiveNote(_Args):
    name: str = Field(min_length=1)


class RenameOrMoveFile(_Args):
    source_path: str = Field(min_length=1)
    destination_path: str = Field(min_length=1)


class GenerateTemplates(_Args):
    overwrite: bool = False


ARGUMENT_MODELS: dict[str, type[_Args]] = {
    "append_note": AppendNote, "add_to_daily": AddToDaily, "create_note": CreateNote,
    "archive_note": ArchiveNote, "rename_or_move_file": RenameOrMoveFile, "generate_templates": GenerateTemplates,
}

INSTRUCTION = (
    "[Nuru] Next step: `{tool}`. Give its arguments as JSON, using only note IDs from the results "
    'above - or {{"cannot_do": "<why>"}} if it can\'t be done.'
)


@dataclass(frozen=True)
class ForcedArguments:
    arguments: dict[str, Any] | None  # valid arguments, ready for gate_write
    cannot_do: str | None  # the model's way out, with its reason
    problems: list[str]  # why the answer was rejected (empty when either of the above is set)


def validate_arguments(tool: str, answer: Any, known_ids: set[str]) -> ForcedArguments:
    if isinstance(answer, dict) and set(answer) == {"cannot_do"}:
        return ForcedArguments(None, str(answer["cannot_do"]), [])
    try:
        arguments = ARGUMENT_MODELS[tool].model_validate(answer).model_dump(exclude_none=True)
    except ValidationError as e:
        return ForcedArguments(None, None, [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()])
    problems = []
    for name in NOTE_ID_FIELDS.get(tool, ()):
        values = arguments.get(name)
        for value in values if isinstance(values, list) else [values] if values else []:
            if value not in known_ids:
                problems.append(f"{name}: {value} was not returned by the last search")
    return ForcedArguments(None, None, problems) if problems else ForcedArguments(arguments, None, [])


def ask_for_arguments(
    chat_json: Callable[[list[dict], dict, Any], str],
    messages: list[dict],
    tool: str,
    input_schema: dict,
    known_ids: list[str],
    think: Any,
) -> ForcedArguments:
    """One forced-schema call for `tool`'s arguments. `chat_json(messages, format, think)` returns the
    model's content; the conversation is left as it was."""
    schema = arguments_schema(tool, input_schema, known_ids)
    content = chat_json([*messages, {"role": "user", "content": INSTRUCTION.format(tool=tool)}], schema, think)
    if not content.strip():
        return ForcedArguments(None, None, ["empty content"])
    try:
        answer = json.loads(content)
    except ValueError as e:
        return ForcedArguments(None, None, [f"not JSON: {e}"])
    return validate_arguments(tool, answer, set(known_ids))
