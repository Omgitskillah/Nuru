from types import SimpleNamespace

import pytest

from core.providers import mcp_tools as mcp_tools_module
from core.providers.mcp_tools import MCPTools

# MCPTools treats server_params opaquely (just forwards it to stdio_client), so
# plain strings stand in for real StdioServerParameters here - each fake
# "server" is identified by its string.
TOOLS: dict[str, list[SimpleNamespace]] = {}
CALLS: list[tuple[str, str, dict]] = []


@pytest.fixture(autouse=True)
def _reset_fake_servers():
    TOOLS.clear()
    CALLS.clear()


class _FakeStdioClient:
    def __init__(self, server_id: str):
        self._server_id = server_id

    async def __aenter__(self):
        return (self._server_id, self._server_id)

    async def __aexit__(self, *exc_info):
        return False


class _FakeClientSession:
    def __init__(self, read, write, client_info=None):
        self._server_id = read

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def initialize(self):
        pass

    async def list_tools(self):
        return SimpleNamespace(tools=TOOLS[self._server_id])

    async def call_tool(self, name, arguments):
        CALLS.append((self._server_id, name, arguments))
        return SimpleNamespace(content=[SimpleNamespace(text=f"{self._server_id}:{name}")])


@pytest.fixture(autouse=True)
def _patch_mcp_sdk(monkeypatch):
    monkeypatch.setattr(mcp_tools_module, "stdio_client", _FakeStdioClient)
    monkeypatch.setattr(mcp_tools_module, "ClientSession", _FakeClientSession)


def test_list_tools_merges_tools_from_every_server():
    TOOLS["server-a"] = [SimpleNamespace(name="tool_a", description="from a", input_schema={"type": "object"})]
    TOOLS["server-b"] = [SimpleNamespace(name="tool_b", description="from b", input_schema={"type": "object"})]

    client = MCPTools(["server-a", "server-b"])
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

    client = MCPTools(["server-a", "server-b"])
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

    client = MCPTools(["server-a"])
    client.connect()
    client.close()

    assert not client._thread.is_alive()
