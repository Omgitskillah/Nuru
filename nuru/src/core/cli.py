"""Terminal CLI that sends whatever text you type to a local Ollama model,
giving it access to the tools exposed by the obsidian and deep-think MCP
servers."""

import json
from collections.abc import Iterator

from core.conversation_store import ConversationStore
from core.session import Event, RetryWithoutThinking, Session, Steered, Text, Thinking, ToolCall

HELP_TEXT = """Available commands:
  /help              Show this message.
  /writes            Show unresolved write attempts. Inspect the vault before resolving one.
  /resolve-write ID completed|not_completed
                     Record your reconciliation; never runs or retries the write.
  /load              Load the model into memory (starting the server if needed) without sending a prompt.
  /show              Print static info about the current model (params, quantization, context window, etc).
  /exit, /bye,       End the session: unload the model, stop the server, and exit.
  /end, /quit
Anything else you type is sent to the model as a prompt (this also loads the model first if it isn't loaded yet)."""


def print_turn(events: Iterator[Event]) -> None:
    """Print a turn as it streams: `[nuru] ` ahead of each stretch of the reply, `[thinking] ` for
    what the model reasoned, `[tool] ` for tool calls, and `[retry] ` where a silent call is being
    asked again with thinking off - everything printed after that line comes from the second call."""
    mid_line = False  # reply text is on screen and its line isn't finished yet
    for event in events:
        if isinstance(event, Text):
            if not mid_line:
                print("[nuru] ", end="", flush=True)
                mid_line = True
            print(event.text, end="", flush=True)
            continue
        if mid_line:
            print()
            mid_line = False
        if isinstance(event, ToolCall):
            print(f"[tool] {event.name}({event.arguments})")
        elif isinstance(event, Thinking):
            print(f"[thinking] {event.text}")
        elif isinstance(event, Steered):
            print(f"[steer] Reasoning stopped ({event.reason}) - asking again with thinking off.")
        elif isinstance(event, RetryWithoutThinking):
            print("[retry] No reply was written - asking again with thinking off.")
        else:
            print(f"[error] {event.message}")
    if mid_line:
        print()


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


def confirm_write(tool: str, arguments: dict) -> bool:
    """Asked when an enforced gate_write wants your OK before a write runs; anything but yes is no."""
    try:
        reply = input(f"[confirm] Run {tool}({json.dumps(arguments, ensure_ascii=False)})? [y/N] ")
    except EOFError:
        return False
    return reply.strip().lower() in ("y", "yes")


def main() -> None:
    session = Session.start()
    session.confirm_write = confirm_write
    try:
        while True:
            usage_pct = _context_usage_percent(session.store, session.conversation_id, session.context_window)
            try:
                text = input(f"[user: {usage_pct}%] ")
            except EOFError:
                break

            if text.strip() in ("/exit", "/bye", "/end", "/quit"):
                session.shutdown("/exit")
                break
            elif text.strip() == "/show":
                info = session.loaded_model_info()
                print("no model loaded" if info is None else json.dumps(info, indent=2))
            elif text.strip() == "/load":
                session.load_model()
            elif text.strip() == "/writes":
                print(json.dumps(session.pending_writes(), indent=2))
            elif text.strip().startswith("/resolve-write "):
                try:
                    _, attempt, state = text.strip().split()
                    session.resolve_write(int(attempt), state)
                    print("Write reconciliation recorded. No write was executed.")
                except ValueError as e:
                    print(f"[error] {e}")
            elif text.strip() == "/help":
                print(HELP_TEXT)
            else:
                print_turn(session.ask(text))
    finally:
        session.close()


if __name__ == "__main__":
    main()
