"""Terminal CLI that sends whatever text you type to a local Ollama model,
giving it access to the tools exposed by the obsidian MCP server."""

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from mcp import StdioServerParameters

from core.cli_logging import CLILogger
from core.conversation_store import ConversationStore
from core.providers.mcp_tools import MCPTools
from core.providers.ollama import (
    chat,
    ensure_model_loaded,
    ensure_server_running,
    is_model_loaded,
    stop_server,
    unload_all_models,
)

load_dotenv()

MODEL = os.environ.get("MODEL", "qwen3:8b")
THINK = os.environ.get("THINK", "true").lower() != "false"

OBSIDIAN_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "servers", "obsidian")
OBSIDIAN_SERVER_PARAMS = StdioServerParameters(command="uv", args=["run", "obsidian-mcp"], cwd=OBSIDIAN_DIR)

LOG_DIR = Path(os.environ.get("NURU_LOG_DIR", Path(__file__).resolve().parents[2] / "logs"))
DB_PATH = Path(os.environ.get("NURU_DB_PATH", Path(__file__).resolve().parents[2] / "db" / "conversation.db"))

DEFAULT_NUM_CTX = 8192
# Leave this fraction of the context window free for the model's response.
RESPONSE_RESERVE = 0.2

# Rough chars-per-token estimate for rejecting an oversized message before it
# ever reaches the model - calibrated against an actual measurement (155,667
# chars of plain English filler text -> ~37,062 prompt tokens once the fixed
# tool-schema overhead is subtracted, i.e. ~4.2 chars/token).
CHARS_PER_TOKEN_ESTIMATE = 4

# DB role -> role the chat API expects.
_ROLE_TO_API = {"user": "user", "nuru": "assistant"}


def _token_budget(context_window: int) -> int:
    return int(context_window * (1 - RESPONSE_RESERVE))


def _estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN_ESTIMATE

HELP_TEXT = """Available commands:
  /help              Show this message.
  /load              Load the model into memory (starting the server if needed) without sending a prompt.
  /show              Print static info about the current model (params, quantization, context window, etc).
  /exit, /bye,       End the session: unload the model, stop the server, and exit.
  /end, /quit
Anything else you type is sent to the model as a prompt (this also loads the model first if it isn't loaded yet)."""


def run_turn(
    tools_client: MCPTools,
    tools: list[dict],
    text: str,
    logger: CLILogger,
    store: ConversationStore,
    conversation_id: int,
    user_message_id: int,
) -> None:
    turn_id = logger.turn_start(text)
    messages = None
    prompt_tokens_recorded = False

    while True:
        server_spawned = ensure_server_running()
        model_info = ensure_model_loaded(MODEL)
        logger.model_lifecycle(server_spawned, model_info)

        num_ctx = (model_info or {}).get("context_window") or DEFAULT_NUM_CTX
        if messages is None:
            history = store.get_recent_messages(conversation_id, _token_budget(num_ctx))
            messages = [{"role": _ROLE_TO_API[m["role"]], "content": m["content"]} for m in history]

        first_chunk = True
        tool_calls = None
        usage: dict = {}
        output_text = ""
        start = time.monotonic()
        try:
            for out in chat(MODEL, messages, num_ctx=num_ctx, think=THINK, tools=tools):
                if isinstance(out, dict):
                    if "tool_calls" in out:
                        tool_calls = out["tool_calls"]
                    else:
                        usage = out
                    continue
                if first_chunk:
                    print("[nuru] ", end="", flush=True)
                    first_chunk = False
                print(out, end="", flush=True)
                output_text += out
            print()
        except requests.exceptions.RequestException as e:
            print(f"\n[error] request to the model failed: {e}")
            print("Your message was not saved - try again, or try a shorter prompt.")
            logger.turn_error(turn_id, str(e))
            store.delete_message(user_message_id)
            return

        if not prompt_tokens_recorded:
            store.update_tokens(user_message_id, usage.get("prompt_tokens"))
            prompt_tokens_recorded = True

        logger.model_call(
            turn_id=turn_id,
            duration_ms=(time.monotonic() - start) * 1000,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            stop_reason=usage.get("stop_reason"),
            tool_calls_requested=[call["function"]["name"] for call in tool_calls] if tool_calls else [],
            output_text=output_text,
        )

        if not tool_calls:
            store.add_message(conversation_id, "nuru", output_text, usage.get("completion_tokens"))
            logger.turn_end(turn_id)
            return

        messages.append({"role": "assistant", "tool_calls": tool_calls})
        for call in tool_calls:
            fn = call["function"]
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args)
            print(f"[tool] {fn['name']}({args})")
            tool_start = time.monotonic()
            try:
                content = tools_client.call_tool(fn["name"], args)
            except Exception as e:
                logger.tool_call(turn_id, fn["name"], args, "error", str(e), (time.monotonic() - tool_start) * 1000)
                raise
            logger.tool_call(turn_id, fn["name"], args, "ok", None, (time.monotonic() - tool_start) * 1000)
            messages.append({"role": "tool", "content": content})


def _context_usage_percent(store: ConversationStore, conversation_id: int, context_window: int) -> int:
    """Percentage of the model's full context window used as of the most recent
    turn (the 20% response reserve doesn't shrink the ceiling here - 100% is
    the full window). Display only - never logged to the DB or the JSONL logs.
    """
    if not context_window:
        return 0
    latest_prompt_tokens = store.get_latest_prompt_tokens(conversation_id)
    if not latest_prompt_tokens:
        return 0
    return min(100, round(latest_prompt_tokens / context_window * 100))


def main() -> None:
    tools_client = MCPTools(OBSIDIAN_SERVER_PARAMS)
    tools_client.connect()
    tools = tools_client.list_tools()

    logger = CLILogger(LOG_DIR, model=MODEL, think=THINK, server_command="uv run obsidian-mcp")
    store = ConversationStore(DB_PATH)
    conversation_id = store.create_conversation()

    server_spawned = ensure_server_running()
    model_loaded_info = ensure_model_loaded(MODEL)
    logger.model_lifecycle(server_spawned, model_loaded_info)

    try:
        while True:
            context_window = (model_loaded_info or {}).get("context_window") or DEFAULT_NUM_CTX
            usage_pct = _context_usage_percent(store, conversation_id, context_window)
            try:
                text = input(f"[user: {usage_pct}%] ")
            except EOFError:
                break

            if text.strip() in ("/exit", "/bye", "/end", "/quit"):
                unload_all_models()
                stop_server()
                logger.session_end(reason="/exit", models_unloaded=True, server_stopped=True)
                break
            elif text.strip() == "/show":
                if is_model_loaded(MODEL):
                    print(json.dumps(ensure_model_loaded(MODEL), indent=2))
                else:
                    print("no model loaded")
            elif text.strip() == "/load":
                server_spawned = ensure_server_running()
                model_loaded_info = ensure_model_loaded(MODEL)
                logger.model_lifecycle(server_spawned, model_loaded_info)
            elif text.strip() == "/help":
                print(HELP_TEXT)
            else:
                budget = _token_budget(context_window)
                estimated_tokens = _estimate_tokens(text)
                if estimated_tokens > budget:
                    print(
                        f"[error] message too long: ~{estimated_tokens} estimated tokens, "
                        f"which exceeds the {budget}-token budget for this model's context window. "
                        "Please shorten it and try again."
                    )
                    continue
                user_message_id = store.add_message(conversation_id, "user", text)
                run_turn(tools_client, tools, text, logger, store, conversation_id, user_message_id)
    finally:
        tools_client.close()
        store.close()


if __name__ == "__main__":
    main()
