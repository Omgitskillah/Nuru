"""Conservative content estimates and one budget for actual model requests.

These are estimates, not tokenizer guarantees. Usage counters remain telemetry;
Ollama can still reject a request, so callers retain bounded overflow recovery.
"""
import json
import math
import re

RESPONSE_RESERVE = 0.2
MESSAGE_FRAMING = 16
REQUEST_FRAMING = 32
MAX_RESPONSE_RESERVE = 2048


def input_budget(window: int) -> int:
    return window - math.ceil(min(window * RESPONSE_RESERVE, MAX_RESPONSE_RESERVE))


class ContextBudgetExceeded(ValueError):
    pass


_DIGIT = re.compile(r"\d")
_SYMBOL = re.compile(r"[^\w\s]")
_LETTERS = re.compile(r"[^\W\d_]+")
_NON_ASCII = re.compile(r"[^\x00-\x7f]")


def estimate_tokens(text: str) -> int:
    """Tokens by what the text is made of: 1.5 a digit, 1 a symbol (punctuation, |, JSON quotes),
    a run of letters 1 or its length / 5 if longer, 1 a newline, and 1 more for every non-ASCII
    character. Measured 2026-11-01 against the Qwen3.5, Granite4.2 and Nemotron tokenizers (Ollama
    prompt_eval_count) on every dummyVault note, system.md, the tool schemas and notes as JSON tool
    results: 1.04-1.27x the largest count, never under. The old 4 bytes a token came to 0.33x on a
    numeric table: tokenizers split numbers and symbols far finer than words."""
    letters = sum(max(1.0, len(run) / 5) for run in _LETTERS.findall(text))
    return math.ceil(1.5 * len(_DIGIT.findall(text)) + len(_SYMBOL.findall(text)) + letters
                     + text.count("\n") + len(_NON_ASCII.findall(text)))


def message_tokens(message: dict) -> int:
    content = message.get("content") or ""
    extra = {k: v for k, v in message.items() if k not in ("role", "content")}
    return MESSAGE_FRAMING + estimate_tokens(content) + (estimate_tokens(json.dumps(extra, ensure_ascii=False)) if extra else 0)


def available_tokens(window: int, messages: list[dict], *, system: str = "", tools=None, fmt=None) -> int:
    """Remaining input capacity after output reserve, framing and the actual request."""
    overhead = REQUEST_FRAMING + sum(message_tokens(m) for m in messages)
    if system and not any(m.get("role") == "system" for m in messages):
        overhead += message_tokens({"role": "system", "content": system})
    for schema in (tools, fmt):
        if schema:
            overhead += estimate_tokens(json.dumps(schema, ensure_ascii=False)) + MESSAGE_FRAMING
    return input_budget(window) - overhead


def require_fit(window: int, messages: list[dict], **kwargs) -> None:
    if available_tokens(window, messages, **kwargs) < 0:
        raise ContextBudgetExceeded("Required request and evidence exceed the estimated context budget. "
                                    "Please shorten the request or retrieve smaller note sections; omitted evidence cannot be used.")
