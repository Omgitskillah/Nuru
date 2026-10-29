"""WP02 M1: model-free compatibility and permission fixtures, before route work."""

import hashlib
import json
from pathlib import Path

import pytest
import jsonschema

from core.service import remote_contract as contract

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "wp02_capture_v1.json").read_text(encoding="utf-8"))


def test_pinned_legacy_receivers_keep_the_public_capture_shape_and_exact_content():
    assert FIXTURE["source_revisions"] == {
        "obsidian_receiver": "04a2774ba9c72cd2599d7147822d51bb26b2fae6",
        "rust_receiver": "046fe9a8b6a0c59693f055f7bb35c9eb66ea85b7",
    }
    uid, content = FIXTURE["uid"], FIXTURE["content"]
    kind, body = contract.parse_request(json.dumps(FIXTURE["legacy_request"]).encode())
    assert kind == "legacy_request" and body == {"content": content}
    for receiver in ("obsidian_receiver", "rust_receiver"):
        assert FIXTURE[receiver]["http_status"] == 201
        note = FIXTURE[receiver]["note"]
        assert note.endswith(content) and note.count(content) == 1
        assert note.startswith("---\nuid: ") and f"Inbox/{uid}.md" == FIXTURE["obsidian_receiver"]["receipt"]["path"]
    # The old Obsidian plugin quotes uid and hashes the exact created bytes.
    plugin = FIXTURE["obsidian_receiver"]
    assert plugin["note"] == f'---\nuid: "{uid}"\n---\n{content}'
    assert plugin["receipt"]["revision"] == hashlib.sha256(plugin["note"].encode()).hexdigest()
    contract.validate("receipt", plugin["receipt"])
    # The Rust receiver leaves uid unquoted and returns a BLAKE3 revision. These
    # bytes and algorithm are legacy data, not a format to rewrite on migration.
    assert FIXTURE["rust_receiver"]["note"] == f"---\nuid: {uid}\n---\n{content}"
    assert FIXTURE["rust_receiver"]["response_fields"] == list(plugin["receipt"])


def test_new_request_and_status_shapes_are_frozen_without_changing_wp01():
    jsonschema.Draft202012Validator.check_schema(json.loads(contract.SCHEMA_PATH.read_text()))
    kind, body = contract.parse_request(json.dumps(FIXTURE["new_request"]).encode())
    assert kind == "idempotent_request" and body["content"] == FIXTURE["content"]
    contract.validate("accepted_capture", FIXTURE["accepted_capture"])
    contract.validate("accepted_capture", FIXTURE["uncertain_capture"])
    for status in FIXTURE["request_statuses"]:
        contract.validate("capture_status", status)
    assert {status["state"] for status in FIXTURE["request_statuses"]} == {
        "accepted", "running", "completed", "uncertain", "gone"
    }
    assert contract.capture_hash(body["content"]) == contract.capture_hash(FIXTURE["legacy_request"]["content"])
    assert contract.capture_key("phone-a", "vault-a", body["request_id"]) != contract.capture_key(
        "phone-b", "vault-a", body["request_id"])
    assert contract.capture_key("phone-a", "vault-a", body["request_id"]) != contract.capture_key(
        "phone-a", "vault-b", body["request_id"])


def test_legacy_error_envelopes_are_frozen():
    assert {(case["http_status"], case["body"]["error"]) for case in FIXTURE["legacy_errors"]} == {
        (400, "invalid_capture"), (401, "unauthorized"), (404, "not_found"),
        (405, "method_not_allowed"), (408, "body_timeout"), (413, "body_rejected"),
        (415, "expected_application_json"), (500, "capture_failed"), (503, "stopping"),
    }
    for case in FIXTURE["legacy_errors"]:
        contract.validate("capture_error", case["body"])


def test_android_setup_contract_preserves_the_plugin_import_route_and_shape():
    setup = FIXTURE["android_setup"]
    assert setup["method"] == "GET"
    assert setup["path_pattern"] == "/v1/android-setup/{64-hex-token}.json"
    assert setup["success_status"] == 200
    assert setup["headers"] == {
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "Content-Type": "application/json",
        "Content-Disposition": 'attachment; filename="remote-notes.json"',
    }
    assert setup["import_shape"] == {
        "version": 90, "category_name": "Remote Notes",
        "shortcut_name": "Capture Obsidian Note", "method": "POST",
        "url_path": "/v1/captures", "authentication": "bearer",
        "requestBodyType": "custom_text", "contentType": "application/json",
        "bodyContent": "{{payload}}", "script_fields": ["codeOnPrepare", "codeOnSuccess"],
    }


def test_unknown_and_foreign_capture_status_are_indistinguishable():
    unknown, foreign = FIXTURE["status_lookup_cases"]
    assert unknown["name"] == "unknown request"
    assert foreign["name"] == "other device's request"
    assert unknown["http_status"] == foreign["http_status"] == 404
    assert unknown["body"] == foreign["body"] == {"ok": False, "error": "not_found"}
    for case in (unknown, foreign):
        contract.validate("capture_error", case["body"])
    assert contract.capture_key("phone-a", "vault-a", foreign["request_id"]) != contract.capture_key(
        foreign["device_id"], "vault-a", foreign["request_id"])


@pytest.mark.parametrize("case", FIXTURE["permission_cases"], ids=lambda case: case["name"])
def test_explicit_permission_matrix(case):
    assert contract.permitted(case["scopes"], case["operation"], active=case.get("active", True),
                              same_owner=case.get("same_owner", True)) is case["allow"]


@pytest.mark.parametrize("body", [
    b"{", b"\xff", b"null", b"[]", b"{}", b'{"content":12}', b'{"content":"  \n"}',
    b'{"content":"x","path":"../outside.md"}', b'{"content":"x","request_id":"short"}',
    b'{"content":"x","request_id":"phone-20261012-0001","vault":"other"}',
    b'{"content":"first","content":"second"}', b'{"content":"\\ud800"}',
    b'\xef\xbb\xbf{"content":"ok"}', b'{"content":"\\u001e"}',
])
def test_invalid_capture_payloads_are_rejected_without_echoing_note_text(body):
    with pytest.raises(ValueError) as error:
        contract.parse_request(body)
    assert "../outside.md" not in str(error.value)


def test_unicode_trim_difference_is_explicit_and_preserves_accepted_content():
    kind, body = contract.parse_request(b'{"content":"\\ufeff"}')
    assert kind == "legacy_request" and body == {"content": "\ufeff"}


def test_oversized_json_is_rejected_before_parse_and_escapes_canonicalize():
    with pytest.raises(ValueError, match="too large"):
        contract.parse_request(b"{" * (contract.MAX_CAPTURE_BYTES + 1))
    _, plain = contract.parse_request(b'{"request_id":"phone-20261012-0001","content":"caf\xc3\xa9"}')
    _, escaped = contract.parse_request(b'{"content":"caf\\u00e9","request_id":"phone-20261012-0001"}')
    assert plain == escaped and contract.capture_hash(plain["content"]) == contract.capture_hash(escaped["content"])


def test_status_never_attaches_a_receipt_until_completion():
    base = {"v": 1, "request_id": "phone-20261012-0001"}
    receipt = FIXTURE["obsidian_receiver"]["receipt"]
    for state in ("accepted", "running", "uncertain", "gone"):
        with pytest.raises(ValueError):
            contract.validate("capture_status", {**base, "state": state, "receipt": receipt})
    with pytest.raises(ValueError):
        contract.validate("capture_status", {**base, "state": "completed"})


def test_an_unknown_capture_message_kind_is_refused_without_echoing_its_content():
    with pytest.raises(ValueError, match="unknown capture message kind") as refused:
        contract.validate("capture_secret", {"content": "private note text"})
    assert "private note text" not in str(refused.value)
