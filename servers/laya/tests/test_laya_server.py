import asyncio

import pytest

from laya_mcp import server


@pytest.fixture
def layer(make_layer, monkeypatch):
    layer, _ = make_layer({"mode": {"answer": 0.99}})
    monkeypatch.setattr(server, "_layer", layer)
    return layer


def test_every_registered_tool_is_either_harness_only_or_for_the_model():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert names == server.HARNESS_TOOLS | server.MODEL_TOOLS
    assert server.MODEL_TOOLS == {"classify_notes"}
    assert not server.HARNESS_TOOLS & server.MODEL_TOOLS


def test_harness_tools_say_so_and_descriptions_are_one_line():
    for tool in asyncio.run(server.mcp.list_tools()):
        assert "\n" not in tool.description
        assert tool.description.startswith("Harness only.") == (tool.name in server.HARNESS_TOOLS)


def test_tool_gate_through_the_protocol_splits_read_and_write_tools(layer):
    tools = [
        {"name": "semantic_search", "description": "Passages", "kind": "read"},
        {"name": "append_note", "description": "Append", "kind": "write"},
    ]
    result = asyncio.run(server.mcp.call_tool("tool_gate", {"phase": "start", "request": "hi", "tools": tools}))
    assert '"mode": "answer"' in str(result) or "'mode': 'answer'" in str(result)


def test_health_reports_versions_and_modes(layer):
    health = server.health()
    assert health["laya"] == {"state": "ready"}
    assert health["question_sets"]["gate_write"] == 1
    assert set(health["decision_points"].values()) == {"shadow"}


def test_bad_input_is_a_tool_error_with_its_message(layer):
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="no vault is configured"):
        server.classify_notes("note_type", folder="nuru")


def test_calls_before_startup_are_a_clear_error(monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError

    monkeypatch.setattr(server, "_layer", None)
    with pytest.raises(ToolError, match="not been started"):
        server.health()
