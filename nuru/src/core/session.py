"""Everything a Nuru chat needs that doesn't depend on how it's shown.

A `Session` brings up the model and its MCP tools, runs one turn at a time (streaming the
reply, running the tool calls the model makes, logging and saving as it goes) and shuts it all
down again. A turn is a generator of events, so cli.py prints them and ui.py renders them.
"""

import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from mcp import StdioServerParameters

from core.cli_logging import CLILogger
from core.conversation_store import ConversationStore
from core.providers.mcp_tools import MCPTools
from core.providers.ollama import (
    chat,
    create_model,
    ensure_model_loaded,
    ensure_server_running,
    is_model_loaded,
    stop_server,
    unload_all_models,
)

load_dotenv()

# BASE_MODEL is the plain Ollama model create_model() builds from; MODEL is
# the derived, custom model - built fresh from Modelfile on every startup -
# that actually gets loaded and chatted with, carrying the tool-use SYSTEM
# prompt baked in.
BASE_MODEL = os.environ.get("NURU_BASE_MODEL", "qwen3:8b")
MODEL = os.environ.get("MODEL", "nuru")
THINK = os.environ.get("THINK", "true").lower() != "false"

MODELFILE_PATH = Path(__file__).resolve().parents[2] / "Modelfile"

OBSIDIAN_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "servers", "obsidian")
OBSIDIAN_SERVER_PARAMS = StdioServerParameters(command="uv", args=["run", "obsidian-mcp"], cwd=OBSIDIAN_DIR)

DEEP_THINK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "servers", "deep-think")
DEEP_THINK_SERVER_PARAMS = StdioServerParameters(command="uv", args=["run", "deep-think-mcp"], cwd=DEEP_THINK_DIR)

# Tools the servers expose that the local model is never offered or allowed to
# call. delete_file_or_directory is a recursive delete on a free-form path, the
# riskiest tool for a model that emits placeholder paths; archive_note does the
# safe version of it (a reversible move). The obsidian server still offers it to
# every other client, where a person is choosing the path.
HIDDEN_FROM_MODEL = frozenset({"delete_file_or_directory"})

LOG_DIR = Path(os.environ.get("NURU_LOG_DIR", Path(__file__).resolve().parents[2] / "logs"))
DB_PATH = Path(os.environ.get("NURU_DB_PATH", Path(__file__).resolve().parents[2] / "db" / "conversation.db"))

DEFAULT_NUM_CTX = 8192
# Ceiling on the context window nuru asks Ollama to allocate. Ollama sizes the KV
# cache from num_ctx, so requesting a large model's full architectural maximum
# (262144 for gemma4:12b) spills part of it onto the CPU and roughly halves
# generation speed.
MAX_NUM_CTX = int(os.environ.get("NURU_MAX_NUM_CTX", 32768))
# Leave this fraction of the context window free for the model's response.
RESPONSE_RESERVE = 0.2

# Rough chars-per-token estimate for rejecting an oversized message before it
# ever reaches the model - calibrated against an actual measurement (155,667
# chars of plain English filler text -> ~37,062 prompt tokens once the fixed
# tool-schema overhead is subtracted, i.e. ~4.2 chars/token).
CHARS_PER_TOKEN_ESTIMATE = 4

# DB role -> role the chat API expects.
_ROLE_TO_API = {"user": "user", "nuru": "assistant"}

# Stands in for the reply when the model ended a turn having written only its thinking, which is
# out as a Thinking event of its own by then.
_NO_REPLY_NOTE = "(No reply was written.)"


def _context_window(model_info: dict | None) -> int:
    """The window nuru actually requests: the model's own maximum, capped at MAX_NUM_CTX."""
    return min((model_info or {}).get("context_window") or DEFAULT_NUM_CTX, MAX_NUM_CTX)


def _token_budget(context_window: int) -> int:
    return int(context_window * (1 - RESPONSE_RESERVE))


def _estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN_ESTIMATE


def _timestamp_line(now: datetime) -> str:
    """The line put ahead of each message sent to the model, so it always knows the local date and time."""
    return f"[Current date and time: {now:%A %Y-%m-%d %H:%M %Z}]"


def _cancelled(cancel: threading.Event | None) -> bool:
    return cancel is not None and cancel.is_set()


@dataclass(frozen=True)
class Text:
    """A chunk of the model's reply."""

    text: str


@dataclass(frozen=True)
class ToolCall:
    """The model asked for a tool. It runs right after this is yielded."""

    name: str
    arguments: dict


@dataclass(frozen=True)
class Thinking:
    """What the model reasoned on its way to a reply or a tool call. One per model call, and it
    arrives once that call is done, so it follows the reply text it was reasoning towards."""

    text: str


@dataclass(frozen=True)
class RetryWithoutThinking:
    """The model ended a call having written only its reasoning, so the turn is being run again
    with thinking off. Whatever follows this came from that second call, not the silent first one."""


@dataclass(frozen=True)
class Failure:
    """The turn couldn't be completed. `message` says why, in words fit to show the user."""

    message: str


Event = Text | ToolCall | Thinking | RetryWithoutThinking | Failure


class Session:
    """One conversation with Nuru: the model, its tools and the stored history.

    Get one from start(), which does the slow setup, and release it with close().
    """

    def __init__(
        self,
        tools: MCPTools,
        tool_definitions: list[dict],
        logger: CLILogger,
        store: ConversationStore,
        conversation_id: int,
    ) -> None:
        self.tools = tools
        self.tool_definitions = tool_definitions
        self.logger = logger
        self.store = store
        self.conversation_id = conversation_id
        self.model_info: dict | None = None

    @classmethod
    def start(cls) -> "Session":
        """Build the custom model, connect the MCP servers, open the log and the database, load the
        model, and return the session that ties them together. Whatever was opened is closed
        again if a later step fails."""
        create_model(MODEL, BASE_MODEL, MODELFILE_PATH)

        with ExitStack() as opened:
            tools = MCPTools([OBSIDIAN_SERVER_PARAMS, DEEP_THINK_SERVER_PARAMS], hidden_tools=HIDDEN_FROM_MODEL)
            tools.connect()
            opened.callback(tools.close)
            tool_definitions = tools.list_tools()

            logger = CLILogger(
                LOG_DIR, model=MODEL, think=THINK, server_command="uv run obsidian-mcp; uv run deep-think-mcp"
            )
            store = ConversationStore(DB_PATH)
            opened.callback(store.close)

            session = cls(tools, tool_definitions, logger, store, store.create_conversation())
            session.load_model()
            opened.pop_all()  # it worked, so the session owns them now
        return session

    @property
    def context_window(self) -> int:
        return _context_window(self.model_info)

    def load_model(self) -> None:
        """Make sure the Ollama server is up and the model is loaded, starting either if it isn't."""
        server_spawned = ensure_server_running()
        self.model_info = ensure_model_loaded(MODEL)
        self.logger.model_lifecycle(server_spawned, self.model_info)

    def loaded_model_info(self) -> dict | None:
        """Static info about the model, or None if it isn't in memory right now."""
        return ensure_model_loaded(MODEL) if is_model_loaded(MODEL) else None

    def ask(self, text: str, cancel: threading.Event | None = None) -> Iterator[Event]:
        """Send `text` to the model, behind a line giving the current date and time, and yield what
        happens next: chunks of its reply, what it reasoned, and the tool calls it makes, then a
        Failure if the turn couldn't be completed. A turn that ends with reasoning but no reply of
        its own says so in one line.

        Setting `cancel` stops the turn at the next chunk (or once the running tool call
        returns); whatever the model had said by then is kept as its reply.
        """
        budget = _token_budget(self.context_window)
        estimated_tokens = _estimate_tokens(text)
        if estimated_tokens > budget:
            yield Failure(
                f"message too long: ~{estimated_tokens} estimated tokens, "
                f"which exceeds the {budget}-token budget for this model's context window. "
                "Please shorten it and try again."
            )
            return

        user_message_id = self.store.add_message(self.conversation_id, "user", text)
        turn_id = self.logger.turn_start(text)
        messages = None
        prompt_tokens_recorded = False
        think = THINK
        # Reasoning from a call that ended with no reply, kept in case the retry below writes
        # nothing either and it has to stand in as the reply after all.
        carried_thinking = ""

        while True:
            self.load_model()

            num_ctx = _context_window(self.model_info)
            if messages is None:
                history = self.store.get_recent_messages(self.conversation_id, _token_budget(num_ctx))
                messages = [{"role": _ROLE_TO_API[m["role"]], "content": m["content"]} for m in history]
                # Only what the model sees carries the timestamp; the saved message stays as typed.
                messages[-1]["content"] = f"{_timestamp_line(datetime.now().astimezone())}\n\n{messages[-1]['content']}"

            tool_calls = None
            usage: dict = {}
            thinking = ""
            output_text = ""
            start = time.monotonic()
            try:
                for out in chat(MODEL, messages, num_ctx=num_ctx, think=think, tools=self.tool_definitions):
                    if _cancelled(cancel):
                        self._end_cancelled(turn_id, output_text)
                        return
                    if isinstance(out, dict):
                        if "tool_calls" in out:
                            tool_calls = out["tool_calls"]
                        elif "thinking" in out:
                            thinking = out["thinking"].strip()
                            if thinking:
                                yield Thinking(thinking)
                        else:
                            usage = out
                        continue
                    output_text += out
                    yield Text(out)
            except requests.exceptions.RequestException as e:
                self.logger.turn_error(turn_id, str(e))
                self.store.delete_message(user_message_id)
                yield Failure(
                    f"request to the model failed: {e}\n"
                    "Your message was not saved - try again, or try a shorter prompt."
                )
                return

            if not prompt_tokens_recorded:
                self.store.update_tokens(user_message_id, usage.get("prompt_tokens"))
                prompt_tokens_recorded = True

            self.logger.model_call(
                turn_id=turn_id,
                duration_ms=(time.monotonic() - start) * 1000,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                stop_reason=usage.get("stop_reason"),
                tool_calls_requested=[call["function"]["name"] for call in tool_calls] if tool_calls else [],
                output_text=output_text,
            )

            if not tool_calls:
                if thinking and not output_text.strip() and think:
                    # Some thinking models write the whole answer into their reasoning and end the
                    # turn there. That is a routing failure rather than a truncation - the call ends
                    # with done_reason "stop" - so asking again with thinking off puts the answer in
                    # the reply instead. `think` stays off for the rest of the turn, which is also
                    # what keeps this to a single retry.
                    carried_thinking = thinking
                    think = False
                    yield RetryWithoutThinking()
                    continue
                if not output_text.strip() and (thinking or carried_thinking):
                    # The retry wrote nothing either. The reasoning is already out as a Thinking
                    # event, so the reply only says that none was written; the history keeps the
                    # reasoning as what the model said.
                    output_text = thinking or carried_thinking
                    yield Text(_NO_REPLY_NOTE)
                self.store.add_message(self.conversation_id, "nuru", output_text, usage.get("completion_tokens"))
                self.logger.turn_end(turn_id)
                return

            messages.append({"role": "assistant", "tool_calls": tool_calls})
            for call in tool_calls:
                fn = call["function"]
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args)
                yield ToolCall(fn["name"], args)
                tool_start = time.monotonic()
                try:
                    content = self.tools.call_tool(fn["name"], args)
                except Exception as e:
                    self.logger.tool_call(turn_id, fn["name"], args, "error", str(e), (time.monotonic() - tool_start) * 1000)
                    raise
                self.logger.tool_call(turn_id, fn["name"], args, "ok", None, (time.monotonic() - tool_start) * 1000)
                messages.append({"role": "tool", "content": content})
                if _cancelled(cancel):
                    self._end_cancelled(turn_id, output_text)
                    return

    def _end_cancelled(self, turn_id: str, output_text: str) -> None:
        """Ends a turn the user stopped. What the model had said so far is kept as its reply so the
        history stays user/nuru alternating and the model knows what it said. No usage figures
        arrive for a cut-short call, so the messages keep a NULL tokens_used."""
        self.store.add_message(self.conversation_id, "nuru", output_text)
        self.logger.turn_end(turn_id)

    def shutdown(self, reason: str) -> None:
        """Unload every model and stop the Ollama server if we started it, and log why the session ended."""
        unload_all_models()
        stop_server()
        self.logger.session_end(reason=reason, models_unloaded=True, server_stopped=True)

    def close(self) -> None:
        """Release the MCP servers and the database. Leaves the model loaded and Ollama running."""
        self.tools.close()
        self.store.close()
