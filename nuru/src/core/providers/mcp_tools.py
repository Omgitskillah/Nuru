"""Sync wrapper around one or more MCP stdio servers.

The MCP SDK is async-only; this runs a ClientSession per server on a single
shared background event loop and exposes blocking `list_tools`/`call_tool`
methods so the rest of the CLI can stay a plain blocking input() loop.
`list_tools()` merges every server's tools into one list and remembers which
session originally reported each tool name, so `call_tool()` can route a
call to the right server without the caller needing to know which server
owns which tool.
"""

import asyncio
import concurrent.futures
import threading
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

_CLIENT_INFO = types.Implementation(name="nuru-agent", version="0.1.0")


class MCPTools:
    def __init__(self, server_params: list[StdioServerParameters]):
        self._server_params = server_params
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._sessions: list[ClientSession] = []
        self._tool_sessions: dict[str, ClientSession] = {}
        self._stop_event: asyncio.Event | None = None
        self._closed_future: concurrent.futures.Future | None = None

    def connect(self) -> None:
        self._thread.start()
        sessions_ready: concurrent.futures.Future = concurrent.futures.Future()
        self._closed_future = concurrent.futures.Future()
        asyncio.run_coroutine_threadsafe(self._run(sessions_ready), self._loop)
        self._sessions = sessions_ready.result()

    async def _run(self, sessions_ready: concurrent.futures.Future) -> None:
        """Owns every session for their whole life: entered and exited by this
        same task, since anyio's cancel scopes require that."""
        self._stop_event = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                sessions = []
                for params in self._server_params:
                    read, write = await stack.enter_async_context(stdio_client(params))
                    session = await stack.enter_async_context(
                        ClientSession(read, write, client_info=_CLIENT_INFO)
                    )
                    await session.initialize()
                    sessions.append(session)
                sessions_ready.set_result(sessions)
                await self._stop_event.wait()
        except Exception as e:
            if not sessions_ready.done():
                sessions_ready.set_exception(e)
        finally:
            self._closed_future.set_result(None)

    def list_tools(self) -> list[dict]:
        """Ollama-format `tools` definitions for every tool every server exposes."""
        tools = []
        self._tool_sessions = {}
        for session in self._sessions:
            result = asyncio.run_coroutine_threadsafe(session.list_tools(), self._loop).result()
            for t in result.tools:
                self._tool_sessions[t.name] = session
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description or "",
                            "parameters": t.input_schema,
                        },
                    }
                )
        return tools

    def call_tool(self, name: str, arguments: dict) -> str:
        """Runs `name` on whichever server reported it and returns its text output."""
        session = self._tool_sessions[name]
        result = asyncio.run_coroutine_threadsafe(session.call_tool(name, arguments), self._loop).result()
        return "\n".join(block.text for block in result.content if hasattr(block, "text"))

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._stop_event.set)
        self._closed_future.result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
