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
    assert server.MODEL_TOOLS == frozenset()
    assert not server.HARNESS_TOOLS & server.MODEL_TOOLS


def test_harness_tools_say_so_and_descriptions_are_one_line():
    for tool in asyncio.run(server.mcp.list_tools()):
        assert "\n" not in tool.description
        assert tool.description.startswith("Harness only.") == (tool.name in server.HARNESS_TOOLS)


def test_tool_gate_through_the_protocol(layer):
    result = asyncio.run(server.mcp.call_tool("tool_gate", {"phase": "start", "request": "hi"}))
    assert '"mode": "answer"' in str(result) or "'mode': 'answer'" in str(result)


def test_health_reports_versions_and_modes(layer):
    health = server.health()
    assert health["laya"] == {"state": "ready"}
    assert health["question_sets"]["gate_write"] == 1
    assert health["decision_points"]["gate_write"] == "off" and health["decision_points"]["assess_effort"] == "off"


def test_bad_input_is_a_tool_error_with_its_message(layer):
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="step_type is 'arguments' or 'answer'"):
        server.assess_effort("r", "bogus", [], {})


def test_calls_before_startup_are_a_clear_error(monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError

    monkeypatch.setattr(server, "_layer", None)
    with pytest.raises(ToolError, match="not been started"):
        server.health()


# ---------------------------------------------------------------- F13: start-up
def test_the_server_builds_its_layer_starts_loading_laya_and_runs(monkeypatch, tmp_path):
    import laya_mcp
    from laya_mcp import server
    started, ran = [], []
    monkeypatch.setattr(server.LayaPredictor, "start", lambda self: started.append(self))
    monkeypatch.setattr(server.mcp, "run", lambda: ran.append(server._layer))
    config = server.load_config()
    monkeypatch.setattr(server, "load_config", lambda: config.model_copy(update={"database": tmp_path / "d.sqlite3"}))
    monkeypatch.setattr(server, "_layer", None)
    laya_mcp.main()  # the package entry point
    layer = server._layer
    try:
        assert len(started) == 1 and ran == [layer] and layer is not None
        assert layer.predictor is started[0] and (tmp_path / "d.sqlite3").exists()
    finally:
        layer.log.close()
