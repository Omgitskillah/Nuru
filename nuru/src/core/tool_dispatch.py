"""Validation and tool-specific outcomes at the model-facing dispatch boundary."""
import json
from dataclasses import dataclass
from itertools import islice

from jsonschema.validators import validator_for

from core.providers.mcp_tools import ToolResult

MUTATIONS = frozenset({"write_note", "rename_or_move_file"})
DOMAIN_TOOLS = frozenset({"calculate", "statistics", "run_python"})


@dataclass(frozen=True)
class ToolOutcome:
    text: str
    success: bool
    kind: str | None = None
    code: str | None = None
    hint: str | None = None
    certainty: str = "completed"  # completed | not_dispatched | not_completed | failed | uncertain
    repairable: bool = False
    attempt_id: int | None = None

    @property
    def must_stop(self) -> bool:
        return self.certainty == "uncertain" or self.kind in {"internal", "replay"}

    def user_message(self, tool: str) -> str:
        if self.certainty == "uncertain":
            return f"The outcome of {tool} is uncertain: {self.hint or self.code}. Do not retry the write."
        if self.kind == "replay":
            return f"Further writes were blocked: {self.hint}."
        if self.kind == "internal":
            return f"{tool} stopped because of an internal error: {self.hint or self.code}."
        return f"{tool} could not be completed: {self.hint or self.code}."


def failure(kind: str, code: str, hint: str, *, certainty="not_dispatched", repairable=False,
            payload: str | None = None, attempt_id: int | None = None) -> ToolOutcome:
    # Preserve domain diagnostics, including partial Python output, but never label them successful.
    text = json.dumps({"ok": False, "error_kind": kind, "code": code, "hint": hint[:1000],
                       "completion_certainty": certainty, "repairable": repairable,
                       **({"payload": payload[:14000]} if payload else {}),
                       **({"attempt_id": attempt_id} if attempt_id is not None else {})})
    return ToolOutcome(text, False, kind, code, hint[:1000], certainty, repairable, attempt_id)


def validators(tools: list[dict]) -> dict:
    compiled = {}
    for tool in tools:
        schema = tool["function"].get("parameters", {})
        validator = validator_for(schema)
        validator.check_schema(schema)
        compiled[tool["function"]["name"]] = validator(schema)
    return compiled


def validate(name: str, arguments, schemas: dict) -> tuple[dict | None, ToolOutcome | None]:
    if not isinstance(name, str) or name not in schemas:
        return None, failure("unavailable", "TOOL_UNAVAILABLE", f"{name!r} is not offered. Choose an available tool.", repairable=True)
    try:
        args = json.loads(arguments, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Non-finite {value}"))) if isinstance(arguments, str) else arguments
    except (ValueError, TypeError) as e:
        return None, failure("arguments", "INVALID_JSON", str(e), repairable=True)
    if not isinstance(args, dict):
        return None, failure("arguments", "EXPECTED_OBJECT", "Tool arguments must be a JSON object.", repairable=True)
    try:
        json.dumps(args, allow_nan=False)
    except (ValueError, TypeError) as e:
        return None, failure("arguments", "INVALID_JSON_VALUE", str(e), repairable=True)
    errors = list(islice(schemas[name].iter_errors(args), 3))
    if errors:
        hint = "; ".join(f"{'.'.join(map(str, e.path)) or 'arguments'}: {e.message}" for e in errors[:3])
        return None, failure("arguments", "SCHEMA_MISMATCH", hint, repairable=True)
    return args, None


def written_path(name: str, outcome: ToolOutcome) -> str | None:
    """The note a successful write actually changed, as the notes server reports it: write_note's
    `path`, rename_or_move_file's `to`. Not what the call asked for: a title, a daily or a folder
    resolves to a file only there. None when it can't be told."""
    if not outcome.success or name not in MUTATIONS:
        return None
    try:
        data = json.loads(outcome.text)
    except (ValueError, TypeError):
        return None
    value = data.get("to" if name == "rename_or_move_file" else "path") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else None


def classify(name: str, result: ToolResult) -> ToolOutcome:
    data = None
    if name in DOMAIN_TOOLS or name in MUTATIONS:
        try:
            # The pinned Python MCP SDK prefixes ToolError text; the receipt remains
            # structured JSON. Never infer certainty from the exception type itself.
            payload = result.text
            prefix = f"Error executing tool {name}: "
            if name in MUTATIONS and result.is_error and payload.startswith(prefix):
                payload = payload[len(prefix):]
            data = json.loads(payload)
        except (ValueError, TypeError):
            pass
    domain_error = isinstance(data, dict) and data.get("ok") is False
    if not result.is_error and not domain_error:
        return ToolOutcome(result.text, True)
    # A mutation error is not proof that nothing changed: server/index cleanup may fail after writing.
    if name in MUTATIONS:
        if (result.is_error and isinstance(data, dict) and data.get("code") == "REJECTED_BEFORE_WRITE"
                and data.get("completion_certainty") == "not_completed"):
            return failure("rejection", "REJECTED_BEFORE_WRITE", str(data.get("message") or result.text),
                           certainty="not_completed", repairable=True, payload=result.text)
        return failure("protocol", "WRITE_OUTCOME_UNCERTAIN", result.text,
                       certainty="uncertain", payload=result.text)
    diagnostic = data.get("error") if isinstance(data, dict) and isinstance(data.get("error"), dict) else data
    code = str(diagnostic.get("code") or diagnostic.get("status") or "TOOL_ERROR") if isinstance(diagnostic, dict) else "TOOL_ERROR"
    hint = str(diagnostic.get("hint") or diagnostic.get("message") or diagnostic.get("error") or result.text) if isinstance(diagnostic, dict) else result.text
    if name == "run_python" and isinstance(data, dict) and data.get("stderr"):
        hint = hint[:200] + "\n" + str(data["stderr"])[:800]
    repairable = name != "run_python" or code in {"SCRIPT_ERROR", "INVALID_INPUT"}
    return failure("domain" if domain_error else "protocol", code, hint, certainty="failed",
                   repairable=repairable, payload=result.text)
