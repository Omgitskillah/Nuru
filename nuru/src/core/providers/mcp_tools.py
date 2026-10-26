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

_CLOSE_SECONDS = 15

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
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._started = False
        self._closed = False
        self._close_lock = threading.Lock()
        self._sessions_ready: concurrent.futures.Future | None = None
        self._sessions: list[ClientSession] = []
        self._tool_sessions: dict[str, ClientSession] = {}
        self._stop_event: asyncio.Event | None = None
        self._closed_future: concurrent.futures.Future | None = None

    def connect(self) -> None:
        if self._started or self._closed:
            raise RuntimeError("MCPTools cannot be connected more than once")
        self._sessions_ready = concurrent.futures.Future()
        self._closed_future = concurrent.futures.Future()
        self._started = True
        try:
            self._thread.start()
            self._sessions = self._sessions_ready.result()
        except BaseException:
            self.close()
            raise

    def _run_loop(self) -> None:
        """The owner task and its loop have the same lifetime, including failed startup."""
        try:
            self._loop.run_until_complete(self._run(self._sessions_ready))
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            finally:
                self._loop.close()

    async def _run(self, sessions_ready: concurrent.futures.Future) -> None:
        """Owns every session for their whole life: entered and exited by this
        same task, since anyio's cancel scopes require that."""
        self._stop_event = asyncio.Event()
        failure = None
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
        except BaseException as e:
            if not sessions_ready.done():
                sessions_ready.set_exception(e)
            else:
                failure = e
        finally:
            if failure is not None:
                self._closed_future.set_exception(failure)
            else:
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
        authorize = getattr(self, "authorize_tool", None)
        if authorize is not None and not authorize(name):
            return ToolResult("Error: device permission does not allow this tool.", is_error=True)
        if not self._tool_sessions:
            self.all_tools()
        session = self._tool_sessions[name]
        result = asyncio.run_coroutine_threadsafe(session.call_tool(name, arguments), self._loop).result()
        text = "\n".join(block.text for block in result.content if hasattr(block, "text"))
        return ToolResult(text, bool(getattr(result, "is_error", False)))

    def close(self) -> None:
        """Close sessions in their owner task, then join and release its event loop.

        Never stop the loop underneath an AnyIO context manager: the SDK must finish
        releasing its subprocess and streams before the loop can be closed.
        """
        with self._close_lock:
            if self._closed:
                return
            if not self._started or self._thread.ident is None:
                self._loop.close()
                self._closed = True
                return
            if self._thread.is_alive() and not self._closed_future.done():
                self._loop.call_soon_threadsafe(self._stop_event.set)
            self._thread.join(timeout=_CLOSE_SECONDS)
            if self._thread.is_alive():
                raise TimeoutError(f"MCP cleanup did not finish within {_CLOSE_SECONDS} seconds")
            self._closed = True
            self._sessions = []
            self._tool_sessions = {}
            self._closed_future.result()
