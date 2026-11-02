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
_SYMBOL = re.compile(r"[^\w\s]|_")
_LETTERS = re.compile(r"[^\W\d_]+")
_NON_ASCII = re.compile(r"[^\x00-\x7f]")
_ASTRAL = re.compile(r"[\U00010000-\U0010ffff]")
_SPACE_RUN = re.compile(r"[^\S\n]{2,}")
_SYMBOL_RUN = re.compile(r"([^\w\s]|_)\1{3,}")
_CHUNK = re.compile(r"\S{16,}")
_MIXED = (re.compile(r"[a-z]"), re.compile(r"[A-Z]"), re.compile(r"\d"))


def _by_class(text: str) -> float:
    repeated = [len(run.group(0)) for run in _SYMBOL_RUN.finditer(text)]
    symbols = len(_SYMBOL.findall(text)) - sum(repeated) + sum(max(1.0, n / 8) for n in repeated)
    letters = sum(max(1.0, len(run) / 5) for run in _LETTERS.findall(text))
    spaces = sum(len(run) / 8 for run in _SPACE_RUN.findall(text))
    return (1.5 * len(_DIGIT.findall(text)) + symbols + letters + spaces + text.count("\n")
            + len(_NON_ASCII.findall(text)) + len(_ASTRAL.findall(text)))


def estimate_tokens(text: str) -> int:
    """Tokens by what the text is made of: 1.5 a digit; 1 a symbol (punctuation, |, _, JSON quotes),
    but a run of 4+ of the same one its length / 8; a run of letters 1, or its length / 5 if longer;
    1 a newline; a run of 2+ other spaces its length / 8; 1 more for each non-ASCII character and 1
    more again for each outside the BMP (emoji). A string of 16+ characters with no space that mixes
    lower and upper case and digits (base64, keys) costs at least its length / 1.2: it barely merges.
    A single space between them is free (tokenizers fold it into the next token), so no run of any
    character is free, however long.

    An estimate, not a tokenizer. Measured 2026-11-01 against the Qwen3.5, Granite4.2 and Nemotron
    tokenizers (Ollama prompt_eval_count of each text alone; script, raw counts and versions in
    nuru-bench token_estimate/): at least the largest count on every text there, adversarial ones
    included, and at most 1.22x it on the vault, prompt and tool texts. The old 4 bytes a token came to
    0.33x on a numeric table. Other text can still come out under: Ollama then refuses the request
    (chat and titles send truncate: false) instead of cutting it."""
    total = _by_class(text)
    for chunk in _CHUNK.findall(text):
        if all(kind.search(chunk) for kind in _MIXED):
            total += max(0.0, len(chunk) / 1.2 - _by_class(chunk))
    return math.ceil(total)


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
