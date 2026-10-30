"""Terminal CLI that sends whatever text you type to a local Ollama model,
giving it access to the tools exposed by the obsidian and math MCP
servers."""

import json
import select
import sys
from collections.abc import Iterator

from core import questions
from core.conversation_store import ConversationStore
from core.questions import MULTI, NO, YES, YES_NO, Question
from core.session import Ask, CallReasked, Event, RetryWithoutThinking, Session, Steered, Text, Thinking, ThinkingChunk, ToolCall

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


def print_turn(events: Iterator[Event]) -> Question | None:
    """Print a turn as it streams: `[nuru] ` ahead of each stretch of the reply, `[thinking] ` ahead
    of what the model reasons as it reasons it, `[tool] ` for tool calls, and `[retry] ` where a
    silent call is being asked again with thinking off - everything printed after that line comes
    from the second call. Returns the question the turn ended on, if the model asked one; answer()
    puts it to you."""
    streaming = None  # "reply" or "thinking": text of that kind is on screen and its line isn't finished
    asked = None
    for event in events:
        if isinstance(event, Text | ThinkingChunk):
            kind = "reply" if isinstance(event, Text) else "thinking"
            if streaming != kind:
                if streaming:
                    print()
                print("[nuru] " if kind == "reply" else "[thinking] ", end="", flush=True)
                streaming = kind
            print(event.text, end="", flush=True)
            continue
        if isinstance(event, Thinking) and streaming == "thinking":
            print()  # its pieces are already on screen: the whole only ends the line
            streaming = None
            continue
        if streaming:
            print()
            streaming = None
        if isinstance(event, ToolCall):
            print(f"[tool] {event.name}({event.arguments})")
        elif isinstance(event, Thinking):
            print(f"[thinking] {event.text}")
        elif isinstance(event, Steered):
            print(f"[steer] Reasoning stopped ({event.reason}) - asking again with thinking off.")
        elif isinstance(event, RetryWithoutThinking):
            print("[retry] No reply was written - asking again with thinking off.")
        elif isinstance(event, CallReasked):
            print("[retry] The model's reply came back empty (a tool call may have been lost) - asking again for a proper call." if event.trigger == "empty_reply"
                  else "[retry] The model wrote a tool call as text - asking again for a proper call.")
        elif isinstance(event, Ask):
            asked = event.question
        else:
            print(f"[error] {event.message}")
    if streaming:
        print()
    return asked


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


def _input_ready(timeout: float) -> bool:
    """Whether a line can be read within `timeout` seconds. Where stdin can't be waited on, it waits
    as long as it takes."""
    try:
        return bool(select.select([sys.stdin], [], [], timeout)[0])
    except (OSError, ValueError, TypeError):
        return True


def _read(prompt: str, timeout: float | None = None) -> str | None:
    """A line you type, or None at EOF or when `timeout` seconds pass first."""
    try:
        if timeout is None:
            return input(prompt)
        print(prompt, end="", flush=True)
        if not _input_ready(timeout):
            print(f"\n[answer] No answer in {timeout:g} s - taken as No.")
            return None
        return input()
    except EOFError:
        return None


def answer(question: Question) -> list[str] | None:
    """Put `question` to you and return the labels you chose, or None for no answer.

    Yes/no takes y or n; Enter gives the recommended answer, or No, and anything but yes is no.
    Options take a number, several comma-separated for a checkbox question (e.g. 1,3), Enter for the
    recommended one, or your own words where the question allows them. A question with a timeout
    returns None once it passes."""
    print(f"[ask] {question.text}" + (f" (No in {question.timeout:g} s)" if question.timeout else ""))
    if question.details:
        print("\n".join("      " + line for line in question.details.splitlines()))
    recommended = question.recommended
    if question.kind == YES_NO:
        default = recommended.label if recommended else NO
        reply = _read(f"[answer] {'[Y/n]' if default == YES else '[y/N]'} ", question.timeout)
        if reply is None:
            return None
        reply = reply.strip().lower()
        return [default] if not reply else [YES] if reply in ("y", "yes") else [NO]
    choices = question.choices
    for number, option in enumerate(choices, 1):
        print(f"      {number}. {option.label}" + (" (recommended)" if option.recommended else "")
              + (f" - {option.description}" if option.description else ""))
    many = question.kind == MULTI
    hint = "choose any, e.g. 1,3" if many else f"choose 1-{len(choices)}"
    hint += ", Enter for the recommended one" if recommended else ", Enter to skip"
    hint += ", or type your own answer" if question.allow_other else ""
    while True:
        reply = _read(f"[answer] ({hint}) ", question.timeout)
        if reply is None:
            return None
        reply = reply.strip()
        if not reply:
            return [recommended.label] if recommended else None
        picks = [pick.strip() for pick in reply.split(",")]
        if (many or len(picks) == 1) and all(pick.isdigit() and 1 <= int(pick) <= len(choices) for pick in picks):
            return list(dict.fromkeys(choices[int(pick) - 1].label for pick in picks))
        if question.allow_other:
            return [reply]
        print("[answer] That isn't one of the options.")


def main() -> None:
    session = Session.start()
    session.answer_question = answer
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
                asked = print_turn(session.ask(text))
                # Nuru ended its turn on a question: your answer is the next message. With no answer
                # you're back at the usual prompt, where anything you type goes to Nuru as before.
                while asked is not None and (chosen := answer(asked)) is not None:
                    asked = print_turn(session.ask(questions.answer_text(chosen)))
    finally:
        session.close()


if __name__ == "__main__":
    main()
