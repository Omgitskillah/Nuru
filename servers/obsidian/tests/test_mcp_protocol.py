"""Tests that go through the actual MCP protocol layer (mcp.list_tools() /
mcp.call_tool()), not just the plain Python functions - this is where the
create_note **type_fields schema bug and the "Error executing tool X" message
-swallowing actually showed up, so it's the level these need to be caught at.
"""

import asyncio
import json
import subprocess

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from obsidian_mcp import server


def _run(coro):
    return asyncio.run(coro)


def test_create_note_schema_has_no_required_wildcard_param(tmp_path, monkeypatch):
    """Regression test for the bug where **type_fields was exposed in the MCP
    schema as a single required string param no caller could satisfy."""
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    tools = _run(server.mcp.list_tools())
    create_note_tool = next(t for t in tools if t.name == "create_note")
    schema = create_note_tool.input_schema

    assert "type_fields" not in schema["properties"]
    assert set(schema["required"]) == {"type", "title", "author"}
    assert {"date", "targets", "session"} <= set(schema["properties"])


def test_create_note_is_callable_through_call_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = _run(server.mcp.call_tool("create_note", {"type": "topic", "title": "X", "author": "nuru"}))

    assert result.is_error is not True
    assert (tmp_path / "nuru" / "x.md").exists()


def test_create_note_with_a_type_specific_field_is_callable_through_call_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = _run(
        server.mcp.call_tool(
            "create_note", {"type": "daily", "title": "X", "author": "user", "date": "2026-09-14"}
        )
    )

    assert result.is_error is not True


def test_anticipated_failure_reaches_the_caller_with_its_message_through_call_tool(tmp_path, monkeypatch):
    """Regression test: a bare ValueError used to arrive as the generic,
    contentless "Error executing tool create_note" - this checks the actual
    message and exception type survive through the real call_tool() path."""
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="ValueError") as exc_info:
        _run(server.mcp.call_tool("create_note", {"type": "bogus", "title": "X", "author": "nuru"}))
    assert "Unknown type" in str(exc_info.value)


def test_new_tools_are_registered_and_only_their_named_params_are_required(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    schemas = {t.name: t.input_schema for t in _run(server.mcp.list_tools())}

    assert schemas["browse_notes"].get("required", []) == []
    assert set(schemas["browse_notes"]["properties"]) == {"type", "limit"}
    assert schemas["all_tags"].get("required", []) == []
    assert schemas["notes_with_tag"]["required"] == ["tag"]
    assert schemas["add_to_daily"]["required"] == ["text"]
    assert schemas["get_links"]["required"] == ["name"]
    assert schemas["archive_note"]["required"] == ["name"]
    assert schemas["read_note"]["required"] == ["name"]
    assert "section" in schemas["read_note"]["properties"]


def test_delete_file_or_directory_is_still_offered_by_the_server(tmp_path, monkeypatch):
    """It is hidden from the local model by the nuru client, not removed here -
    other clients (a person in Claude Desktop) still need it."""
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert "delete_file_or_directory" in {t.name for t in _run(server.mcp.list_tools())}


def test_the_new_tools_work_end_to_end_through_call_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    monkeypatch.setattr(server, "_today", lambda: "2026-09-20")

    def call(name, args=None):
        result = _run(server.mcp.call_tool(name, args or {}))
        assert result.is_error is not True, result
        return result

    call("add_to_daily", {"text": "- 09:00 [[Plan]] kicked off"})
    call("create_note", {"type": "project", "title": "Plan", "author": "both", "tags": ["mcp"], "body": "## Goal\n\nShip"})
    assert "user/daily-2026-09-20.md" in str(call("browse_notes", {"type": "daily"}))
    assert "mcp" in str(call("all_tags"))
    assert "shared/plan.md" in str(call("notes_with_tag", {"tag": "#MCP"}))
    assert "Ship" in str(call("read_note", {"name": "plan", "section": "goal"}))
    assert "user/daily-2026-09-20.md" in str(call("get_links", {"name": "Plan"}))
    assert "archive/shared/plan.md" in str(call("archive_note", {"name": "Plan"}))


def test_a_read_note_miss_reaches_the_caller_with_its_closest_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    (tmp_path / "Nuru MCP.md").write_text("x")

    with pytest.raises(ToolError, match="FileNotFoundError") as exc_info:
        _run(server.mcp.call_tool("read_note", {"name": "Nuru MCPP"}))

    assert "Nuru MCP.md" in str(exc_info.value)


def _stub_rg(monkeypatch, stdout="", returncode=0, stderr=""):
    """Stand in for the rg binary - what's under test is the protocol layer around it."""
    monkeypatch.setattr(server.shutil, "which", lambda name: "/fake/rg")
    monkeypatch.setattr(
        server.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, returncode, stdout, stderr)
    )


def test_find_in_notes_takes_only_a_pattern_and_returns_its_matches_through_call_tool(tmp_path, monkeypatch):
    (tmp_path / "Plan.md").write_text("Ship it - café\n")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    event = {
        "type": "match",
        "data": {
            "path": {"text": str(tmp_path / "Plan.md")},
            "lines": {"text": "Ship it - café\n"},
            "line_number": 1,
            "absolute_offset": 0,
            "submatches": [{"match": {"text": "Ship"}, "start": 0, "end": 4}],
        },
    }
    _stub_rg(monkeypatch, stdout=json.dumps(event) + "\n")

    schema = next(t for t in _run(server.mcp.list_tools()) if t.name == "find_in_notes").input_schema
    result = _run(server.mcp.call_tool("find_in_notes", {"pattern": "ship"}))

    assert schema["required"] == ["pattern"]
    assert set(schema["properties"]) == {"pattern"}
    assert result.is_error is not True
    assert "Plan.md" in str(result) and "Ship it - café" in str(result)


def test_find_in_notes_bad_pattern_reaches_the_caller_with_ripgreps_message_through_call_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    _stub_rg(monkeypatch, returncode=2, stderr="rg: regex parse error: unclosed group")

    with pytest.raises(ToolError, match="ValueError") as exc_info:
        _run(server.mcp.call_tool("find_in_notes", {"pattern": "("}))

    assert "unclosed group" in str(exc_info.value)
