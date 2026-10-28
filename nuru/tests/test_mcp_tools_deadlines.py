"""F05: every MCP request has a deadline and runs alone; a request that overruns or is stopped retires the
wrapper, closing its servers, and nothing is sent after that. Real stdio servers where the SDK's own
timeouts matter, fakes where only Nuru's guard can be reached."""
import asyncio
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from mcp import MCPError, StdioServerParameters

from core.providers import mcp_tools
from core.providers.mcp_tools import MCPTools, ToolCallStopped, ToolsUnavailable

# A server whose `stall` and `write_note` never return. Every call it receives is logged first.
_PEER = '''
import sys, anyio
from mcp.server.mcpserver import MCPServer
log = sys.argv[1]
server = MCPServer("deadline-test")
def seen(name):
    with open(log, "a") as f:
        f.write(name + "\\n")
@server.tool()
def echo(text: str) -> str:
    seen("echo")
    return text
@server.tool()
async def stall() -> str:
    seen("stall")
    await anyio.sleep(3600)
    return "never"
@server.tool()
async def write_note(content: str) -> str:
    seen("write_note")
    await anyio.sleep(3600)
    return "never"
server.run(transport="stdio")
'''
_SILENT = "import time; time.sleep(3600)"  # starts, but never answers initialize


@pytest.fixture
def processes(monkeypatch):
    """Every real server process the SDK starts, to check none is left running."""
    from mcp.client import stdio
    started = []
    create = stdio._create_platform_compatible_process

    async def capture(*args, **kwargs):
        process = await create(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(stdio, "_create_platform_compatible_process", capture)
    return started


@pytest.fixture
def peer(tmp_path):
    log = tmp_path / "calls.log"
    log.touch()
    params = StdioServerParameters(command=sys.executable, args=["-c", _PEER, str(log)])
    return SimpleNamespace(params=params, calls=lambda: log.read_text().split())


def _wait_until(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.01)


def _short(monkeypatch, **seconds):
    for name, value in {"_GRACE_SECONDS": 1, **seconds}.items():
        monkeypatch.setattr(mcp_tools, name, value)


def test_a_stalled_read_times_out_retires_the_tools_and_stops_the_server(peer, processes, monkeypatch):
    _short(monkeypatch, _READ_SECONDS=0.5)
    client = MCPTools([peer.params], frozenset({"stall", "echo"}))
    client.connect()
    started = time.monotonic()

    with pytest.raises(TimeoutError, match="the stall tool took longer than 0.5 s"):
        client.call_tool("stall", {})

    assert time.monotonic() - started < 5
    assert client.retired and "took longer than 0.5 s" in client.retired
    assert processes[0].returncode is not None  # closed, not left working on it
    with pytest.raises(ToolsUnavailable, match="an earlier request was abandoned"):
        client.call_tool("echo", {"text": "after"})
    with pytest.raises(ToolsUnavailable):
        client.list_tools()
    assert peer.calls() == ["stall"]
    client.close()  # already closed: nothing left to do


def test_a_request_waiting_its_turn_is_never_sent_once_the_tools_retire(peer, processes, monkeypatch):
    _short(monkeypatch, _READ_SECONDS=1)
    client = MCPTools([peer.params], frozenset({"stall", "echo"}))
    client.connect()
    client.list_tools()
    outcomes = {}

    def run(name, arguments):
        try:
            outcomes[name] = client.call_tool(name, arguments)
        except Exception as error:
            outcomes[name] = error

    stalled = threading.Thread(target=run, args=("stall", {}))
    stalled.start()
    _wait_until(lambda: peer.calls() == ["stall"])
    waiting = threading.Thread(target=run, args=("echo", {"text": "queued"}))
    waiting.start()
    stalled.join(10)
    waiting.join(10)

    assert isinstance(outcomes["stall"], TimeoutError)
    assert isinstance(outcomes["echo"], ToolsUnavailable)
    assert peer.calls() == ["stall"]  # the queued echo never reached the server


def test_a_write_gets_the_longer_deadline_and_stopping_it_retires_the_tools(peer, processes, monkeypatch):
    _short(monkeypatch, _READ_SECONDS=0.3, _WRITE_SECONDS=30)
    client = MCPTools([peer.params], frozenset({"write_note"}), writes=frozenset({"write_note"}))
    client.connect()
    client.cancel = threading.Event()
    threading.Timer(0.8, client.cancel.set).start()  # past the read deadline: a write isn't held to it
    started = time.monotonic()

    with pytest.raises(ToolCallStopped, match="the write_note tool was stopped while it ran"):
        client.call_tool("write_note", {"content": "x"})

    assert 0.8 <= time.monotonic() - started < 5
    assert peer.calls() == ["write_note"]  # it was sent: it may have happened
    assert client.retired and processes[0].returncode is not None


def test_a_request_after_the_turn_was_stopped_is_not_sent_and_retires_nothing(peer, processes):
    client = MCPTools([peer.params], frozenset({"echo"}))
    client.connect()
    try:
        client.list_tools()
        client.cancel = threading.Event()
        client.cancel.set()
        with pytest.raises(ToolsUnavailable, match="the turn was stopped"):
            client.call_tool("echo", {"text": "late"})
        assert client.retired is None and peer.calls() == []
        client.cancel = None
        assert client.call_tool("echo", {"text": "next turn"}) == "next turn"
    finally:
        client.close()


def test_a_required_server_that_never_answers_fails_startup_in_bounded_time(processes, monkeypatch):
    _short(monkeypatch, _STARTUP_SECONDS=0.5, _GRACE_SECONDS=5)  # the SDK takes 2 s to stop a server
    client = MCPTools([StdioServerParameters(command=sys.executable, args=["-c", _SILENT])], frozenset())
    started = time.monotonic()

    with pytest.raises(BaseExceptionGroup) as raised:  # the SDK's stdio task group wraps it
        client.connect()

    assert raised.group_contains(MCPError, match="Request 'initialize' timed out")
    assert time.monotonic() - started < 5
    assert processes[0].returncode is not None and client._loop.is_closed()


def test_an_optional_server_that_never_answers_or_cannot_start_is_left_out(peer, processes, monkeypatch):
    _short(monkeypatch, _OPTIONAL_STARTUP_SECONDS=0.5)
    silent = StdioServerParameters(command=sys.executable, args=["-c", _SILENT])
    missing = StdioServerParameters(command="/nonexistent/python-mcp", args=[])
    client = MCPTools([peer.params], frozenset({"echo"}), optional=[silent, missing])
    try:
        client.connect()

        assert [command for command, _ in client.unavailable] == [f"{sys.executable} -c {_SILENT}", "/nonexistent/python-mcp"]
        assert "timed out" in client.unavailable[0][1]
        assert [t["function"]["name"] for t in client.list_tools()] == ["echo"]
        assert client.call_tool("echo", {"text": "still here"}) == "still here"
        assert processes[1].returncode is not None  # the silent one was stopped at once
    finally:
        client.close()
    assert all(process.returncode is not None for process in processes)


# Starts properly, then stalls or refuses listing its tools (argv[1]: "stall" or "error").
_BAD_LISTING = '''
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "initialize":
        result = {"protocolVersion": message["params"]["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "bad-listing", "version": "1"}}
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
    elif message.get("method") == "tools/list" and sys.argv[1] == "error":
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32603, "message": "no tools today"}}), flush=True)
'''


def _bad_listing(mode):
    return StdioServerParameters(command=sys.executable, args=["-c", _BAD_LISTING, mode])


@pytest.mark.parametrize("mode, why", [("stall", "timed out"), ("error", "no tools today")])
def test_an_optional_server_that_fails_listing_its_tools_is_left_out_alone(peer, processes, monkeypatch, mode, why):
    _short(monkeypatch, _OPTIONAL_STARTUP_SECONDS=0.5)
    client = MCPTools([peer.params], frozenset({"echo"}), optional=[_bad_listing(mode)])
    try:
        client.connect()

        ((command, reason),) = client.unavailable
        assert command.endswith(mode) and why in reason
        assert [t["function"]["name"] for t in client.list_tools()] == ["echo"]
        assert client.call_tool("echo", {"text": "required still works"}) == "required still works"
        assert client.retired is None and processes[1].returncode is not None
    finally:
        client.close()


def test_a_required_server_that_stalls_listing_its_tools_fails_startup_in_bounded_time(processes, monkeypatch):
    _short(monkeypatch, _STARTUP_SECONDS=0.5, _GRACE_SECONDS=5)
    client = MCPTools([_bad_listing("stall")], frozenset())
    started = time.monotonic()

    with pytest.raises(BaseExceptionGroup) as raised:
        client.connect()

    assert raised.group_contains(MCPError, match="Request 'tools/list' timed out")
    assert time.monotonic() - started < 5 and processes[0].returncode is not None


# ------------------------------------------------- Nuru's own guard, past a deadline the SDK never enforces
class Transport:
    async def __aenter__(self):
        return "read", "write"

    async def __aexit__(self, *exc):
        return False


class Peer:
    """A ClientSession that ignores every deadline it is given."""
    initialize_forever = False
    call = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        if Peer.initialize_forever:
            await asyncio.Event().wait()

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name="tool", description="", input_schema={})])

    async def call_tool(self, name, arguments, read_timeout_seconds=None):
        return await Peer.call()


@pytest.fixture
def ignoring_peer(monkeypatch):
    Peer.initialize_forever, Peer.call = False, None
    monkeypatch.setattr(mcp_tools, "stdio_client", lambda params: Transport())
    monkeypatch.setattr(mcp_tools, "ClientSession", Peer)


async def _forever():
    await asyncio.Event().wait()


def test_a_call_the_sdk_never_times_out_is_stopped_by_nurus_own_deadline(ignoring_peer, monkeypatch):
    _short(monkeypatch, _READ_SECONDS=0.1, _GRACE_SECONDS=0.1)
    Peer.call = _forever
    client = MCPTools(["peer"], frozenset({"tool"}))
    client.connect()

    with pytest.raises(TimeoutError, match="the tool tool took longer than 0.1 s"):
        client.call_tool("tool", {})
    assert client.retired and client._loop.is_closed()


def test_startup_the_sdk_never_times_out_is_stopped_by_nurus_own_deadline(ignoring_peer, monkeypatch):
    _short(monkeypatch, _STARTUP_SECONDS=0.1, _GRACE_SECONDS=0.1)
    Peer.initialize_forever = True
    client = MCPTools(["peer"], frozenset())

    with pytest.raises(TimeoutError, match="the tool servers didn't start within 0.2 s"):
        client.connect()
    assert client._loop.is_closed()


def test_a_server_timing_itself_out_at_startup_is_reported_as_is(ignoring_peer, monkeypatch):
    async def own_timeout(self):
        raise TimeoutError("the server's own deadline")
    monkeypatch.setattr(Peer, "initialize", own_timeout)
    client = MCPTools(["peer"], frozenset())

    with pytest.raises(TimeoutError, match="the server's own deadline"):
        client.connect()


def test_another_mcp_error_is_passed_on_without_retiring_the_tools(ignoring_peer):
    async def refuse():
        raise MCPError(code=-32603, message="internal error in the tool")
    Peer.call = refuse
    client = MCPTools(["peer"], frozenset({"tool"}))
    client.connect()
    try:
        with pytest.raises(MCPError, match="internal error in the tool"):
            client.call_tool("tool", {})
        assert client.retired is None
    finally:
        client.close()


def test_a_call_cut_off_by_close_from_another_thread_ends_without_waiting_out_its_deadline(ignoring_peer, monkeypatch):
    _short(monkeypatch, _READ_SECONDS=60)
    Peer.call = _forever
    client = MCPTools(["peer"], frozenset({"tool"}))
    client.connect()
    threading.Timer(0.3, client.close).start()
    started = time.monotonic()

    with pytest.raises(ConnectionAbortedError, match="the tools were closed while it ran"):
        client.call_tool("tool", {})
    assert time.monotonic() - started < 5


def test_an_optional_server_whose_cleanup_fails_is_still_left_out(ignoring_peer, monkeypatch):
    class BadTransport(Transport):
        async def __aexit__(self, *exc):
            raise OSError("pipe already gone")

    async def refuse(self):
        raise ConnectionError("refused")
    monkeypatch.setattr(mcp_tools, "stdio_client", lambda params: Transport() if params == "required" else BadTransport())
    monkeypatch.setattr(Peer, "initialize", lambda self: refuse(self) if self.optional else _nothing())
    monkeypatch.setattr(Peer, "__init__", lambda self, read, write, **kwargs: setattr(self, "optional", kwargs["read_timeout_seconds"] == mcp_tools._OPTIONAL_STARTUP_SECONDS))
    client = MCPTools(["required"], frozenset(), optional=["optional"])
    client.connect()
    try:
        ((command, why),) = client.unavailable
        assert command == "optional" and why == "ConnectionError: refused"
    finally:
        client.close()


async def _nothing():
    return None


def test_a_retirement_whose_close_overruns_still_raises_the_timeout_and_says_so(ignoring_peer, monkeypatch):
    _short(monkeypatch, _READ_SECONDS=0.1, _GRACE_SECONDS=0.1, _CLOSE_SECONDS=0.05)
    release = None

    async def slow_exit(self, *exc):
        nonlocal release
        release = asyncio.Event()
        await release.wait()
    Peer.call = _forever
    monkeypatch.setattr(Peer, "__aexit__", slow_exit)
    client = MCPTools(["peer"], frozenset({"tool"}))
    client.connect()

    with pytest.raises(TimeoutError, match="took longer than 0.1 s") as raised:
        client.call_tool("tool", {})

    assert any("closing the tool servers also failed: TimeoutError" in note for note in raised.value.__notes__)
    _wait_until(lambda: release is not None)
    client._loop.call_soon_threadsafe(release.set)
    monkeypatch.setattr(mcp_tools, "_CLOSE_SECONDS", 5)
    client.close()
    assert client._loop.is_closed()
