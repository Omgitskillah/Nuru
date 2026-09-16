"""Terminal CLI that sends whatever text you type to a local Ollama model,
giving it access to the tools exposed by the obsidian MCP server."""

import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from mcp import StdioServerParameters

from core.cli_logging import CLILogger
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
print(f"think={THINK}")

OBSIDIAN_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "servers", "obsidian")
OBSIDIAN_SERVER_PARAMS = StdioServerParameters(command="uv", args=["run", "obsidian-mcp"], cwd=OBSIDIAN_DIR)

LOG_DIR = Path(os.environ.get("NURU_LOG_DIR", Path(__file__).resolve().parents[2] / "logs"))

HELP_TEXT = """Available commands:
  /help              Show this message.
  /load              Load the model into memory (starting the server if needed) without sending a prompt.
  /show              Print static info about the current model (params, quantization, context window, etc).
  /exit, /bye,       End the session: unload the model, stop the server, and exit.
  /end, /quit
Anything else you type is sent to the model as a prompt (this also loads the model first if it isn't loaded yet)."""


def run_turn(tools_client: MCPTools, tools: list[dict], text: str, logger: CLILogger) -> None:
    turn_id = logger.turn_start(text)
    messages = [{"role": "user", "content": text}]

    while True:
        server_spawned = ensure_server_running()
        model_info = ensure_model_loaded(MODEL)
        logger.model_lifecycle(server_spawned, model_info)

        first_chunk = True
        tool_calls = None
        usage: dict = {}
        output_text = ""
        start = time.monotonic()
        for out in chat(MODEL, messages, think=THINK, tools=tools):
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


def main() -> None:
    tools_client = MCPTools(OBSIDIAN_SERVER_PARAMS)
    tools_client.connect()
    tools = tools_client.list_tools()

    logger = CLILogger(LOG_DIR, model=MODEL, think=THINK, server_command="uv run obsidian-mcp")

    try:
        while True:
            try:
                text = input("> ")
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
                run_turn(tools_client, tools, text, logger)
    finally:
        tools_client.close()


if __name__ == "__main__":
    main()
