"""Sync wrapper around one or more MCP stdio servers.

The MCP SDK is async-only; this runs a ClientSession per server on a single
shared background event loop and exposes blocking `list_tools`/`call_tool`
methods so the rest of the CLI can stay a plain blocking input() loop.
`list_tools()` merges every server's tools into one list and remembers which
session originally reported each tool name, so `call_tool()` can route a
call to the right server without the caller needing to know which server
owns which tool.

`model_tools` is the allowlist of tools the model may have: only those are in
`list_tools()` and callable through `call_tool()`, so a tool a server adds later -
or one the model names anyway (from training, or an old prompt) - can't be reached
until it is added here on purpose. Nuru's harness reaches every tool, the
decision layer's included, through `call_harness_tool()`; the model never does.
"""

import asyncio
import concurrent.futures
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

_CLIENT_INFO = types.Implementation(name="nuru-agent", version="0.1.0")


@dataclass(frozen=True)
class ToolResult:
    """A tool's text output, and whether the server reported it as an error."""

    text: str
    is_error: bool = False


class MCPTools:
    def __init__(self, server_params: list[StdioServerParameters], model_tools: frozenset[str]):
        self._server_params = server_params
        self._model_tools = model_tools
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
        """Ollama-format `tools` definitions for the allowlisted tools - what the model is offered."""
        return [t for t in self.all_tools() if t["function"]["name"] in self._model_tools]

    def all_tools(self) -> list[dict]:
        """Ollama-format `tools` definitions for every tool every server exposes. For the harness."""
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
        """Runs an allowlisted tool for the model and returns its text output."""
        return self.call_tool_result(name, arguments).text

    def call_tool_result(self, name: str, arguments: dict) -> ToolResult:
        """Runs an allowlisted tool for the model; anything else is refused without reaching a server."""
        if name not in self._model_tools:
            return ToolResult(f"Error: the tool '{name}' is not available.", is_error=True)
        return self.call_harness_tool(name, arguments)

    def call_harness_tool(self, name: str, arguments: dict) -> ToolResult:
        """Runs any tool on whichever server reported it. Only the harness calls this."""
        if not self._tool_sessions:
            self.all_tools()
        session = self._tool_sessions[name]
        result = asyncio.run_coroutine_threadsafe(session.call_tool(name, arguments), self._loop).result()
        text = "\n".join(block.text for block in result.content if hasattr(block, "text"))
        return ToolResult(text, bool(getattr(result, "is_error", False)))

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._stop_event.set)
        self._closed_future.result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
