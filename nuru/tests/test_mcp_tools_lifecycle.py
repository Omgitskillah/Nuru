"""MCP ownership regressions: failures must release threads, loops and real peers."""
import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest
from mcp import StdioServerParameters

from core.providers import mcp_tools
from core.providers.mcp_tools import MCPTools


class Transport:
    async def __aenter__(self):
        return 'read', 'write'

    async def __aexit__(self, *exc):
        return False


class Session:
    exits = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.exits.append(self)
        return False

    async def initialize(self):
        pass

    async def list_tools(self):
        return SimpleNamespace(tools=[])


@pytest.fixture
def fake_peer(monkeypatch):
    Session.exits = []
    monkeypatch.setattr(mcp_tools, 'stdio_client', lambda p: Transport())
    monkeypatch.setattr(mcp_tools, 'ClientSession', Session)


def released(client):
    assert not client._thread.is_alive()
    assert client._loop.is_closed()
    client.close()  # cleanup after either successful or failed cleanup is idempotent


def test_close_before_connect_releases_loop_and_rejects_later_connect():
    client = MCPTools([], frozenset())
    client.close()
    released(client)
    with pytest.raises(RuntimeError, match='more than once'):
        client.connect()


def test_successful_close_releases_loop_and_rejects_reconnect(fake_peer):
    client = MCPTools(['peer'], frozenset())
    client.connect()
    with pytest.raises(RuntimeError, match='more than once'):
        client.connect()
    client.close()
    released(client)
    assert len(Session.exits) == 1
    assert client._sessions == []


@pytest.mark.parametrize('stage', ['transport', 'initialize', 'second_transport'])
def test_failed_start_releases_partial_sessions_and_loop(fake_peer, monkeypatch, stage):
    async def fail_initialize(self):
        raise ValueError('initialize failed')

    def transport(p):
        if stage == 'transport' or p == 'second':
            raise ValueError('transport failed')
        return Transport()

    if stage == 'initialize':
        monkeypatch.setattr(Session, 'initialize', fail_initialize)
    else:
        monkeypatch.setattr(mcp_tools, 'stdio_client', transport)
    client = MCPTools(['first', 'second'] if stage == 'second_transport' else ['first'], frozenset())
    with pytest.raises(ValueError, match='failed'):
        client.connect()
    released(client)
    assert len(Session.exits) == (0 if stage == 'transport' else 1)


def test_cleanup_exception_is_reported_after_loop_and_thread_released(fake_peer, monkeypatch):
    async def fail_exit(self, *args):
        raise ValueError('cleanup failed')

    monkeypatch.setattr(Session, '__aexit__', fail_exit)
    client = MCPTools(['peer'], frozenset())
    client.connect()
    with pytest.raises(ValueError, match='cleanup failed'):
        client.close()
    released(client)


def test_thread_start_failure_releases_loop(fake_peer, monkeypatch):
    client = MCPTools([], frozenset())
    def fail():
        raise RuntimeError('thread start failed')
    monkeypatch.setattr(client._thread, 'start', fail)
    with pytest.raises(RuntimeError, match='thread start failed'):
        client.connect()
    released(client)


def test_real_sdk_close_reaps_stdio_peer(monkeypatch):
    """Exercise actual AnyIO owner-task teardown; fakes cannot enforce its cancel scopes."""
    from mcp.client import stdio
    processes = []
    create = stdio._create_platform_compatible_process
    async def capture_process(*args, **kwargs):
        process = await create(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(stdio, '_create_platform_compatible_process', capture_process)
    script = '''
from mcp.server.mcpserver import MCPServer
server=MCPServer('lifecycle-test')
@server.tool()
def echo(text: str) -> str:
    return text
server.run(transport='stdio')
'''
    client = MCPTools([StdioServerParameters(command=sys.executable, args=['-c', script])],
                      frozenset({'echo'}))
    try:
        client.connect()
        assert client.list_tools()[0]['function']['name'] == 'echo'
        assert client.call_tool('echo', {'text': 'round trip'}) == 'round trip'
    finally:
        client.close()
    released(client)
    assert len(processes) == 1
    assert processes[0].returncode is not None


def test_slow_cleanup_keeps_owner_loop_alive_and_can_finish_later(fake_peer, monkeypatch):
    entered = threading.Event()
    release = None
    async def slow_exit(self, *args):
        nonlocal release
        release = asyncio.Event()
        entered.set()
        await release.wait()
    monkeypatch.setattr(Session, '__aexit__', slow_exit)
    monkeypatch.setattr(mcp_tools, '_CLOSE_SECONDS', 0.03)
    client = MCPTools(['peer'], frozenset())
    client.connect()
    try:
        with pytest.raises(TimeoutError, match='cleanup did not finish'):
            client.close()
        assert entered.is_set()
        assert client._thread.is_alive() and not client._loop.is_closed()
    finally:
        if release is not None:
            client._loop.call_soon_threadsafe(release.set)
        monkeypatch.setattr(mcp_tools, '_CLOSE_SECONDS', 5)
        client.close()
    released(client)


def test_permission_refusal_never_reaches_peer_even_without_discovery(fake_peer):
    client = MCPTools(['peer'], frozenset({'write_note'}))
    client.authorize_tool = lambda name: False
    client.connect()
    try:
        result = client.call_tool_result('write_note', {'content': 'must not be sent'})
        assert result.is_error and 'permission' in result.text
    finally:
        client.close()
    released(client)
