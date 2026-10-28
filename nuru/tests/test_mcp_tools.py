from types import SimpleNamespace

import pytest

from core.providers import mcp_tools as mcp_tools_module
from core.providers.mcp_tools import MCPTools

# MCPTools treats server_params opaquely (just forwards it to stdio_client), so
# plain strings stand in for real StdioServerParameters here - each fake
# "server" is identified by its string.
TOOLS: dict[str, list[SimpleNamespace]] = {}
CALLS: list[tuple[str, str, dict]] = []
TIMEOUTS: list[tuple[str, str, float | None]] = []  # (server, "session" or the tool, its read timeout)


@pytest.fixture(autouse=True)
def _reset_fake_servers():
    TOOLS.clear()
    CALLS.clear()
    TIMEOUTS.clear()


class _FakeStdioClient:
    def __init__(self, server_id: str):
        self._server_id = server_id

    async def __aenter__(self):
        return (self._server_id, self._server_id)

    async def __aexit__(self, *exc_info):
        return False


class _FakeClientSession:
    def __init__(self, read, write, client_info=None, read_timeout_seconds=None):  # the real SDK's parameters
        self._server_id = read
        TIMEOUTS.append((read, "session", read_timeout_seconds))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def initialize(self):
        pass

    async def list_tools(self):
        return SimpleNamespace(tools=TOOLS[self._server_id])

    async def call_tool(self, name, arguments, read_timeout_seconds=None):
        CALLS.append((self._server_id, name, arguments))
        TIMEOUTS.append((self._server_id, name, read_timeout_seconds))
        return SimpleNamespace(content=[SimpleNamespace(text=f"{self._server_id}:{name}")], is_error=name == "failing_tool")


@pytest.fixture(autouse=True)
def _patch_mcp_sdk(monkeypatch):
    monkeypatch.setattr(mcp_tools_module, "stdio_client", _FakeStdioClient)
    monkeypatch.setattr(mcp_tools_module, "ClientSession", _FakeClientSession)


def test_list_tools_merges_tools_from_every_server():
    TOOLS["server-a"] = [SimpleNamespace(name="tool_a", description="from a", input_schema={"type": "object"})]
    TOOLS["server-b"] = [SimpleNamespace(name="tool_b", description="from b", input_schema={"type": "object"})]

    client = MCPTools(["server-a", "server-b"], model_tools=frozenset({"tool_a", "tool_b"}))
    client.connect()
    try:
        tools = client.list_tools()
    finally:
        client.close()

    assert tools == [
        {"type": "function", "function": {"name": "tool_a", "description": "from a", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "tool_b", "description": "from b", "parameters": {"type": "object"}}},
    ]


def test_call_tool_routes_to_the_server_that_reported_it():
    TOOLS["server-a"] = [SimpleNamespace(name="tool_a", description="", input_schema={})]
    TOOLS["server-b"] = [SimpleNamespace(name="tool_b", description="", input_schema={})]

    client = MCPTools(["server-a", "server-b"], model_tools=frozenset({"tool_a", "tool_b"}))
    client.connect()
    try:
        client.list_tools()
        result = client.call_tool("tool_b", {"x": 1})
    finally:
        client.close()

    assert result == "server-b:tool_b"
    assert CALLS == [("server-b", "tool_b", {"x": 1})]


def test_close_stops_the_background_thread():
    TOOLS["server-a"] = [SimpleNamespace(name="tool_a", description="", input_schema={})]

    client = MCPTools(["server-a"], model_tools=frozenset())
    client.connect()
    client.close()

    assert not client._thread.is_alive()


def test_only_allowlisted_tools_are_offered_to_the_model():
    TOOLS["server-a"] = [
        SimpleNamespace(name="safe_tool", description="ok", input_schema={}),
        SimpleNamespace(name="dangerous_tool", description="no", input_schema={}),
        SimpleNamespace(name="tool_added_later", description="new", input_schema={}),
    ]

    client = MCPTools(["server-a"], model_tools=frozenset({"safe_tool"}))
    client.connect()
    try:
        names = [t["function"]["name"] for t in client.list_tools()]
        everything = [t["function"]["name"] for t in client.all_tools()]
    finally:
        client.close()

    assert names == ["safe_tool"]
    assert everything == ["safe_tool", "dangerous_tool", "tool_added_later"]


def test_call_tool_refuses_a_tool_off_the_allowlist_without_reaching_the_server():
    TOOLS["server-a"] = [
        SimpleNamespace(name="safe_tool", description="ok", input_schema={}),
        SimpleNamespace(name="dangerous_tool", description="no", input_schema={}),
    ]

    client = MCPTools(["server-a"], model_tools=frozenset({"safe_tool"}))
    client.connect()
    try:
        client.list_tools()
        refused = client.call_tool_result("dangerous_tool", {"path": "x"})
        allowed = client.call_tool("safe_tool", {})
    finally:
        client.close()

    assert refused.is_error and "not available" in refused.text
    assert allowed == "server-a:safe_tool"
    assert CALLS == [("server-a", "safe_tool", {})]


def test_call_tool_refuses_a_tool_off_the_allowlist_even_before_list_tools_was_called():
    TOOLS["server-a"] = [SimpleNamespace(name="dangerous_tool", description="no", input_schema={})]

    client = MCPTools(["server-a"], model_tools=frozenset())
    client.connect()
    try:
        refused = client.call_tool("dangerous_tool", {})
    finally:
        client.close()

    assert "not available" in refused
    assert CALLS == []


def test_the_harness_reaches_tools_the_model_cannot():
    TOOLS["server-a"] = [SimpleNamespace(name="tool_gate", description="harness only", input_schema={})]

    client = MCPTools(["server-a"], model_tools=frozenset())
    client.connect()
    try:
        result = client.call_harness_tool("tool_gate", {"phase": "start"})
    finally:
        client.close()

    assert result.text == "server-a:tool_gate" and not result.is_error
    assert CALLS == [("server-a", "tool_gate", {"phase": "start"})]


def test_a_tool_error_is_reported_as_one():
    TOOLS["server-a"] = [SimpleNamespace(name="failing_tool", description="", input_schema={})]

    client = MCPTools(["server-a"], model_tools=frozenset({"failing_tool"}))
    client.connect()
    try:
        result = client.call_tool_result("failing_tool", {})
    finally:
        client.close()

    assert result.is_error
