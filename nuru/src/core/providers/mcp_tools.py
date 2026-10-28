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

Every request has a deadline, and one runs at a time. A request that runs past its
deadline, or is stopped through `cancel`, retires the whole wrapper: its servers are
closed, since one may still be working on it, and every later request - including
one already waiting its turn - fails with ToolsUnavailable without being sent. The
owner (Session) starts a fresh wrapper for its next turn. A write that timed out or
was stopped may or may not have happened; it is never retried here.
"""

import asyncio
import concurrent.futures
import threading
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass

from mcp import ClientSession, MCPError, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.types import REQUEST_TIMEOUT

_CLOSE_SECONDS = 15
_STARTUP_SECONDS = 30  # a server's initialize, and listing its tools
_OPTIONAL_STARTUP_SECONDS = 120  # the same for an optional server, which may start cold (Docker, torch)
_READ_SECONDS = 120  # a search may wait for Ollama to load a model
_WRITE_SECONDS = 300  # a write takes milliseconds, but a false timeout leaves it uncertain
_GRACE_SECONDS = 5  # past the SDK's own deadline, in case it never fires
_POLL_SECONDS = 0.1  # how often a waiting request looks at `cancel`

_CLIENT_INFO = types.Implementation(name="nuru-agent", version="0.1.0")


class ToolsUnavailable(ConnectionError):
    """The wrapper was retired or closed before this request was sent: it didn't run."""


class ToolCallStopped(ConnectionError):
    """`cancel` was set while the request ran: it may or may not have taken effect."""


def _describe(params) -> str:
    return " ".join([params.command, *params.args]) if hasattr(params, "command") else str(params)


@dataclass(frozen=True)
class ToolResult:
    """A tool's text output, and whether the server reported it as an error."""

    text: str
    is_error: bool = False


class MCPTools:
    def __init__(self, server_params: list[StdioServerParameters], model_tools: frozenset[str], *,
                 optional: list[StdioServerParameters] = (), writes: frozenset[str] = frozenset()):
        """`optional` servers may fail to start without failing connect(): they're left out, and
        listed in `unavailable`. A tool in `writes` gets the longer deadline."""
        self._server_params = server_params
        self._optional = list(optional)
        self._model_tools = model_tools
        self._writes = writes
        self._admission = threading.Lock()  # one request at a time
        self.retired: str | None = None  # why, once a request timed out or was stopped
        self.unavailable: list[tuple[str, str]] = []  # (command, why) of optional servers that didn't start
        self.cancel: threading.Event | None = None  # set by the owner for a turn: stops its running request
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._started = False
        self._closed = False
        self._close_lock = threading.Lock()
        self._sessions_ready: concurrent.futures.Future | None = None
        self._sessions: list[ClientSession] = []
        self._tool_sessions: dict[str, ClientSession] = {}
        self._stop_event = asyncio.Event()
        self._closed_future: concurrent.futures.Future | None = None
        self._owner: asyncio.Task | None = None

    def connect(self) -> None:
        started_here = False
        try:
            # close() may run from another thread during startup. Publish the futures
            # and start the thread under the same lock, without holding it while waiting.
            with self._close_lock:
                if self._started or self._closed:
                    raise RuntimeError("MCPTools cannot be connected more than once")
                self._sessions_ready = concurrent.futures.Future()
                self._closed_future = concurrent.futures.Future()
                self._started = True
                started_here = True
                self._thread.start()
            limit = (len(self._server_params) * _STARTUP_SECONDS + len(self._optional) * _OPTIONAL_STARTUP_SECONDS
                     + _GRACE_SECONDS)
            try:
                self._sessions = self._sessions_ready.result(timeout=limit)
            except concurrent.futures.TimeoutError:
                if self._sessions_ready.done():
                    raise  # a server's own startup timeout
                raise TimeoutError(f"the tool servers didn't start within {limit:g} s") from None
        except BaseException as error:
            if not started_here:
                raise
            try:
                self.close()
            except BaseException as cleanup:
                error.add_note(f"MCP cleanup also failed: {type(cleanup).__name__}: {cleanup}")
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
        self._owner = asyncio.current_task()
        failure = None
        try:
            async with AsyncExitStack() as stack:
                sessions = []
                for params, optional in [(p, False) for p in self._server_params] + [(p, True) for p in self._optional]:
                    peer = await stack.enter_async_context(AsyncExitStack())  # this server's, to close alone
                    try:
                        read, write = await peer.enter_async_context(stdio_client(params))
                        session = await peer.enter_async_context(ClientSession(
                            read, write, client_info=_CLIENT_INFO,
                            read_timeout_seconds=_OPTIONAL_STARTUP_SECONDS if optional else _STARTUP_SECONDS))
                        await session.initialize()
                    except Exception as error:
                        if not optional:
                            raise
                        try:
                            await peer.aclose()
                        except Exception as cleanup:
                            error.add_note(f"closing it also failed: {type(cleanup).__name__}: {cleanup}")
                        self.unavailable.append((_describe(params), f"{type(error).__name__}: {error}"))
                        continue
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
        if self.retired or self._closed:
            raise ToolsUnavailable("the tools weren't listed: " + (self.retired or "the tools are closed"))
        for session in self._sessions:
            result = self._request(lambda: session.list_tools(), _OPTIONAL_STARTUP_SECONDS, "listing the tools")
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
        seconds = _WRITE_SECONDS if name in self._writes else _READ_SECONDS
        result = self._request(lambda: session.call_tool(name, arguments, read_timeout_seconds=seconds),
                               seconds, f"the {name} tool")
        text = "\n".join(block.text for block in result.content if hasattr(block, "text"))
        return ToolResult(text, bool(getattr(result, "is_error", False)))

    def _request(self, start, seconds: float, what: str):
        """Run the coroutine `start()` makes on the owner loop, alone, and wait for it: at most
        `seconds` (the SDK's deadline) plus a grace, and only until `cancel` is set. Either retires
        the wrapper. Nothing is sent once it's retired or closed."""
        with self._admission:
            if self.retired or self._closed or not self._started:
                raise ToolsUnavailable(f"{what} wasn't run: " + (self.retired or "the tools are closed"))
            if self.cancel is not None and self.cancel.is_set():
                raise ToolsUnavailable(f"{what} wasn't run: the turn was stopped")
            future = asyncio.run_coroutine_threadsafe(start(), self._loop)
            deadline = time.monotonic() + seconds + _GRACE_SECONDS
            while True:
                done, _ = concurrent.futures.wait([future], timeout=_POLL_SECONDS)
                if done:
                    try:
                        return future.result()
                    except MCPError as error:
                        if error.error.code != REQUEST_TIMEOUT:
                            raise
                        stopped = TimeoutError(f"{what} took longer than {seconds:g} s")
                        self._retire(stopped, future)
                        raise stopped from error
                if self.cancel is not None and self.cancel.is_set():
                    stopped = ToolCallStopped(f"{what} was stopped while it ran")
                elif time.monotonic() > deadline:
                    stopped = TimeoutError(f"{what} took longer than {seconds:g} s")
                elif self._closed:  # closed under it: the loop is gone, so it can never finish
                    raise ConnectionAbortedError(f"{what} was cut off: the tools were closed while it ran")
                else:
                    continue
                self._retire(stopped, future)
                raise stopped

    def _retire(self, error: BaseException, future: concurrent.futures.Future) -> None:
        """Stop `future`'s request and close every server: one may still be working on it."""
        self.retired = f"an earlier request was abandoned ({error})"
        future.cancel()
        try:
            self.close()
        except BaseException as cleanup:
            error.add_note(f"closing the tool servers also failed: {type(cleanup).__name__}: {cleanup}")

    def _stop_owner(self) -> None:
        """On the owner loop: let a started owner close its sessions in order; one still starting may
        be waiting on a server that never answers, so it is cancelled instead."""
        if self._owner is None:  # not running yet
            self._loop.call_soon(self._stop_owner)
        elif self._sessions_ready.done():
            self._stop_event.set()
        else:
            self._owner.cancel()

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
                try:
                    self._loop.call_soon_threadsafe(self._stop_owner)
                except RuntimeError:
                    # The owner can finish between the liveness check and scheduling.
                    if not self._loop.is_closed():
                        raise
            self._thread.join(timeout=_CLOSE_SECONDS)
            if self._thread.is_alive():
                raise TimeoutError(f"MCP cleanup did not finish within {_CLOSE_SECONDS} seconds")
            self._closed = True
            self._sessions = []
            self._tool_sessions = {}
            self._closed_future.result()
