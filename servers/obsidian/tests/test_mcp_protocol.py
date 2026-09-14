"""Tests that go through the actual MCP protocol layer (mcp.list_tools() /
mcp.call_tool()), not just the plain Python functions - this is where the
create_note **type_fields schema bug and the "Error executing tool X" message
-swallowing actually showed up, so it's the level these need to be caught at.
"""

import asyncio

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
