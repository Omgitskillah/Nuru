"""WP02 capture and device-permission contract. HTTP enforcement arrives in M2/M3.

This module is deliberately independent of the model and the vault writer. It makes
the M1 compatibility and authorization decisions executable before those routes
exist, so the later service work has one contract to implement.
"""

import hashlib
import json
from collections.abc import Collection
from functools import cache
from pathlib import Path

import jsonschema

from core.service import protocol

SCHEMA_PATH = Path(__file__).with_name("capture-v1.schema.json")
MAX_CAPTURE_BYTES = 256 * 1024  # both pinned RemoteNotes receivers accept 256 KiB

# Permissions are checked at the route and, for model tool calls, at dispatch. A
# chat credential alone cannot make a model read or write a note. In M3 the service
# must also bind resource ownership to the authenticated device/operator and vault.
REQUIRED_CAPABILITIES = {
    "service_status": frozenset({"chat:use"}),
    "capture_create": frozenset({"capture:create"}),
    "capture_status": frozenset({"capture:create"}),
    "chat_create": frozenset({"chat:use", "notes:read"}),
    "chat_submit": frozenset({"chat:use", "notes:read"}),
    "chat_request_status": frozenset({"chat:use", "notes:read"}),
    "chat_history": frozenset({"chat:use", "notes:read"}),
    "chat_events": frozenset({"chat:use", "notes:read"}),
    "chat_answer": frozenset({"chat:use", "notes:read", "notes:write"}),
    "chat_stop": frozenset({"chat:use", "notes:read"}),
    "chat_note_read": frozenset({"chat:use", "notes:read"}),
    "chat_note_write": frozenset({"chat:use", "notes:read", "notes:write"}),
    "note_preview": frozenset({"notes:read"}),
}


def permitted(capabilities: Collection[str], operation: str, *, active: bool,
              same_owner: bool) -> bool:
    """An unknown operation, inactive credential or foreign resource fails closed.

    ``same_owner`` means the authenticated device for capture status, and the
    configured operator/vault for chat and note resources. The host must derive
    this from stored records, never from a client-supplied device or vault name.
    """
    required = REQUIRED_CAPABILITIES.get(operation)
    return bool(active and same_owner and required and required <= set(capabilities))


@cache
def _validator(kind: str) -> jsonschema.Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator({**schema, "$ref": f"#/$defs/{kind}"})


def validate(kind: str, value: object) -> None:
    """Validate a capture message; keep rejected content out of diagnostics."""
    if kind not in {"legacy_request", "idempotent_request", "receipt", "accepted_capture",
                    "capture_error", "capture_status"}:
        raise ValueError("unknown capture message kind")
    if next(_validator(kind).iter_errors(value), None) is not None:
        raise ValueError("invalid capture message")
    if kind in {"legacy_request", "idempotent_request"} and not value["content"].strip():
        raise ValueError("capture content is blank")


def parse_request(raw: bytes) -> tuple[str, dict]:
    """Decode an old content-only or new request-ID capture, preserving content."""
    if len(raw) > MAX_CAPTURE_BYTES:
        raise ValueError("capture body is too large")

    def unique_fields(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate capture field")
            value[key] = item
        return value

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_fields)
    except (UnicodeError, ValueError) as error:
        raise ValueError("invalid capture JSON") from error
    kind = "idempotent_request" if isinstance(value, dict) and "request_id" in value else "legacy_request"
    validate(kind, value)
    try:
        value["content"].encode("utf-8")
    except UnicodeError as error:
        raise ValueError("invalid capture Unicode") from error
    return kind, value


def capture_key(device_id: str, vault_id: str, request_id: str) -> str:
    """Scope a durable capture request to its device and the host-configured vault."""
    return protocol.request_key(device_id, vault_id, "capture_create", request_id)


def capture_hash(content: str) -> str:
    """Hash decoded content, so equivalent JSON escapes identify one request."""
    return hashlib.sha256(protocol.canonical({"content": content})).hexdigest()
