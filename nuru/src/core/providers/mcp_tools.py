"""Sync wrapper around an MCP stdio server.

The MCP SDK is async-only; this runs a ClientSession on a background event
loop and exposes blocking `list_tools`/`call_tool` methods so the rest of
the CLI can stay a plain blocking input() loop.
"""

import asyncio
import concurrent.futures
import threading
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPTools:
    def __init__(self, server_params: StdioServerParameters):
        self._server_params = server_params
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._session: ClientSession | None = None
        self._stop_event: asyncio.Event | None = None
        self._closed_future: concurrent.futures.Future | None = None

    def connect(self) -> None:
        self._thread.start()
        session_ready: concurrent.futures.Future = concurrent.futures.Future()
        self._closed_future = concurrent.futures.Future()
        asyncio.run_coroutine_threadsafe(self._run(session_ready), self._loop)
        self._session = session_ready.result()

    async def _run(self, session_ready: concurrent.futures.Future) -> None:
        """Owns the session for its whole life: entered and exited by this
        same task, since anyio's cancel scopes require that."""
        self._stop_event = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                read, write = await stack.enter_async_context(stdio_client(self._server_params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                session_ready.set_result(session)
                await self._stop_event.wait()
        except Exception as e:
            if not session_ready.done():
                session_ready.set_exception(e)
        finally:
            self._closed_future.set_result(None)

    def list_tools(self) -> list[dict]:
        """Ollama-format `tools` definitions for every tool the server exposes."""
        result = asyncio.run_coroutine_threadsafe(self._session.list_tools(), self._loop).result()
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.input_schema,
                },
            }
            for t in result.tools
        ]

    def call_tool(self, name: str, arguments: dict) -> str:
        """Runs `name` on the server and returns its text output."""
        result = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, arguments), self._loop
        ).result()
        return "\n".join(block.text for block in result.content if hasattr(block, "text"))

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._stop_event.set)
        self._closed_future.result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
