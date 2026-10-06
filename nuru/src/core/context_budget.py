"""Conservative content estimates and one budget for actual model requests.

These are estimates, not tokenizer guarantees. Usage counters remain telemetry;
Ollama can still reject a request, so callers retain bounded overflow recovery.
"""
import json
import math

RESPONSE_RESERVE = 0.2
MESSAGE_FRAMING = 16
REQUEST_FRAMING = 32
MAX_RESPONSE_RESERVE = 2048


def input_budget(window: int) -> int:
    return window - math.ceil(min(window * RESPONSE_RESERVE, MAX_RESPONSE_RESERVE))


class ContextBudgetExceeded(ValueError):
    pass


def estimate_tokens(text: str) -> int:
    # UTF-8 bytes make Unicode less optimistic than the English chars/4 heuristic.
    return math.ceil(len(text.encode("utf-8")) / 4)


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
