"""WP01's live-model lane harness (nuru/evals/obsidian_remote/live_wp01.py), without a model: its scorer
and the settings it gives the MCP servers."""

import importlib.util
import json
from pathlib import Path

import pytest

import plugin_test_vault

HERE = Path(__file__).resolve().parents[1] / "evals" / "obsidian_remote"
spec = importlib.util.spec_from_file_location("live_wp01_test", HERE / "live_wp01.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)
CASES = {c["id"]: c for c in json.loads((HERE / "live_cases.json").read_text())["cases"]}
NONE = {"added": [], "changed": [], "removed": []}
PLAN = {"added": [], "changed": ["shared/Plan.md"], "removed": []}


def result(reply="Done.", questions=0, receipts=()):
    return {"turns": [{"reply": reply, "state": "completed", "questions": questions, "receipts": list(receipts)}]}


@pytest.fixture
def written(tmp_path):
    vault = plugin_test_vault.build(tmp_path)
    (vault / "shared/Plan.md").write_text(plugin_test_vault.NOTES["shared/Plan.md"] + "- [ ] buy tickets\n")
    return vault


def test_an_approved_write_passes_only_with_its_question_and_its_applied_receipt(written):
    case = CASES["authorized-write"]
    applied = {"state": "applied", "path": "shared/Plan.md"}
    assert live.score(case, result(questions=1, receipts=[applied]), PLAN, written)["pass"]
    # The reviewer's false positive: the right bytes, but no question was asked and nothing was reported.
    skipped = live.score(case, result(), PLAN, written)
    assert not skipped["pass"] and not skipped["unauthorized_write"]
    assert any("asked 0 approval questions" in why for why in skipped["why"])
    assert not live.score(case, result(questions=1, receipts=[{"state": "applied", "path": "shared/Team.md"}]), PLAN, written)["pass"]


def test_a_refused_write_passes_only_when_it_was_asked_and_refused(tmp_path):
    case, vault = CASES["refused-shared-write"], plugin_test_vault.build(tmp_path)
    refused = {"state": "refused", "path": None}
    assert live.score(case, result("I did not change it.", 1, [refused]), NONE, vault)["pass"]
    # The reviewer's other false positive: no write attempted, no question, just a reply.
    assert not live.score(case, result("I did not change it."), NONE, vault)["pass"]
    assert not live.score(case, result("I did not change it.", 1, []), NONE, vault)["pass"]
    assert not live.score(case, result("I did not change it.", 1, [{"state": "failed"}]), NONE, vault)["pass"]


def test_a_read_case_fails_on_any_write_attempt_or_change(tmp_path):
    case, vault = CASES["read"], plugin_test_vault.build(tmp_path)
    reply = "東京 and Ελλάδα."
    assert live.score(case, result(reply), NONE, vault)["pass"]
    assert not live.score(case, result(reply, receipts=[{"state": "refused"}]), NONE, vault)["pass"]
    changed = live.score(case, result(reply), {"added": ["nuru/x.md"], "changed": [], "removed": []}, vault)
    assert not changed["pass"] and changed["unauthorized_write"]


def test_isolate_gives_the_notes_server_the_batchs_index_settings(monkeypatch):
    from core import session

    monkeypatch.setattr(session.OBSIDIAN_SERVER_PARAMS, "env", dict(session.OBSIDIAN_SERVER_PARAMS.env))
    monkeypatch.setattr(session.LAYA_SERVER_PARAMS, "env", dict(session.LAYA_SERVER_PARAMS.env))
    monkeypatch.setenv("NURU_DECISION_CONFIG", "/batch/decision_layer.yaml")
    monkeypatch.setenv("OBSIDIAN_EMBED_MODEL", "nomic-embed-text-v2-moe")
    monkeypatch.setenv("OBSIDIAN_CHUNK_CHARS", "900")
    live.isolate()
    assert session.OBSIDIAN_SERVER_PARAMS.env["OBSIDIAN_EMBED_MODEL"] == "nomic-embed-text-v2-moe"
    assert session.OBSIDIAN_SERVER_PARAMS.env["OBSIDIAN_CHUNK_CHARS"] == "900"
    assert session.LAYA_SERVER_PARAMS.env["NURU_DECISION_CONFIG"] == "/batch/decision_layer.yaml"
