"""Terminal CLI that sends whatever text you type to a local Ollama model,
giving it access to the tools exposed by the obsidian MCP server."""

import json
import os

from dotenv import load_dotenv
from mcp import StdioServerParameters

from core.providers.mcp_tools import MCPTools
from core.providers.ollama import chat, ensure_model_loaded, ensure_server_running, stop_server, unload_all_models

load_dotenv()

MODEL = os.environ.get("MODEL", "qwen3:8b")
THINK = os.environ.get("THINK", "true").lower() != "false"
print(f"think={THINK}")

OBSIDIAN_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "servers", "obsidian")
OBSIDIAN_SERVER_PARAMS = StdioServerParameters(command="uv", args=["run", "obsidian-mcp"], cwd=OBSIDIAN_DIR)


def run_turn(tools_client: MCPTools, tools: list[dict], text: str) -> None:
    messages = [{"role": "user", "content": text}]

    while True:
        ensure_server_running()
        ensure_model_loaded(MODEL)

        first_chunk = True
        tool_calls = None
        for out in chat(MODEL, messages, think=THINK, tools=tools):
            if isinstance(out, dict):
                tool_calls = out.get("tool_calls") or tool_calls
                continue
            if first_chunk:
                print("[nuru] ", end="", flush=True)
                first_chunk = False
            print(out, end="", flush=True)
        print()

        if not tool_calls:
            return

        messages.append({"role": "assistant", "tool_calls": tool_calls})
        for call in tool_calls:
            fn = call["function"]
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args)
            print(f"[tool] {fn['name']}({args})")
            content = tools_client.call_tool(fn["name"], args)
            messages.append({"role": "tool", "content": content})


def main() -> None:
    tools_client = MCPTools(OBSIDIAN_SERVER_PARAMS)
    tools_client.connect()
    tools = tools_client.list_tools()

    try:
        while True:
            try:
                text = input("> ")
            except EOFError:
                break

            if text.strip() == "/exit":
                unload_all_models()
                stop_server()
                break

            run_turn(tools_client, tools, text)
    finally:
        tools_client.close()


if __name__ == "__main__":
    main()
