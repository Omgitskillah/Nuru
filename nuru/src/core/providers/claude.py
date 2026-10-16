"""Claude as Nuru's main model (NURU_MAIN_MODEL=anthropic:<id>), through the shared claude-access
provider. `chat()` has ollama.chat()'s signature and yields the same things, so the session's turn
loop, tools, checks and logs work unchanged:

- text chunks, and {"thinking_chunk": ...} while the model reasons;
- {"tool_calls": [...]} (each with the API's call `id`), then {"provider_content": [...]}: the whole
  assistant content - thinking blocks with their signatures, text and tool_use blocks - which the
  session keeps on the tool-call message for the rest of the turn, because the API requires earlier
  thinking blocks back unchanged during tool use;
- {"thinking": ...} with the full reasoning, then a usage dict.

The messages it receives are Ollama-shaped (role/content, assistant tool_calls, role "tool" results in
call order); to_anthropic() turns them into Anthropic content blocks. The system prompt (system.md, the
same text the Modelfiles carry) and the tool list are marked for prompt caching.

Every request is also written locally, whole, to NURU_LOG_DIR/claude-requests-<date>.jsonl: what was
sent (messages, tool names), the model, the thinking setting and the usage, so it can be checked
afterwards what left the machine.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import requests
from claude_access.claude import ClaudeProvider
from claude_access.models import MAIN_MODELS

PREFIX = "anthropic:"
# Thinking shares max_tokens with the reply; too small and the reply is cut off after the thinking.
MAX_TOKENS = 16000
LOG_DIR = Path(os.environ.get("NURU_LOG_DIR", Path(__file__).resolve().parents[3] / ".logs"))
_CACHE = {"type": "ephemeral"}
_STOP_REASONS = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "stop", "max_tokens": "length"}
_NOT_RUN = "Not run."

_provider: ClaudeProvider | None = None


class ClaudeError(requests.exceptions.RequestException):
    """A failed Claude request. A RequestException, so the session handles it as it does a failed
    Ollama request: the turn stops with the error and the user's message isn't kept."""


def model_id(main_model: str) -> str:
    """The model ID in NURU_MAIN_MODEL ("anthropic:claude-haiku-5-5" -> "claude-haiku-5-5")."""
    name = main_model.removeprefix(PREFIX)
    if name not in MAIN_MODELS:
        raise ValueError(f"Unsupported main model {main_model!r}; use one of "
                         + ", ".join(PREFIX + m for m in MAIN_MODELS))
    return name


def model_info(model: str) -> dict:
    """What ollama.ensure_model_loaded() returns, for a Claude model."""
    return {"parameter_size": "?", "quantization_level": "api", "family": "claude", "supports_thinking": True,
            "context_window": MAIN_MODELS[model]["context_window"], "embedding_length": None}


def provider() -> ClaudeProvider:
    global _provider
    if _provider is None:
        _provider = ClaudeProvider()
    return _provider


def _arguments(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def to_anthropic(messages: list[dict]) -> list[dict]:
    """Ollama-shaped messages as Anthropic ones. Each "tool" message answers the oldest unanswered
    tool_use of the assistant message before it; any left unanswered get a "Not run." result, as the
    API requires one per tool_use. Messages of the same role in a row are merged."""
    out: list[dict] = []
    pending: list[str] = []

    def add(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    def answer_pending() -> None:
        add("user", [{"type": "tool_result", "tool_use_id": i, "content": _NOT_RUN} for i in pending])
        pending.clear()

    for index, message in enumerate(messages):
        role, content = message.get("role"), message.get("content") or ""
        if role == "assistant":
            answer_pending()
            blocks = list(message.get("provider_content") or [])
            if not blocks:
                if content.strip():
                    blocks.append({"type": "text", "text": content})
                for n, call in enumerate(message.get("tool_calls") or []):
                    fn = call.get("function") or {}
                    blocks.append({"type": "tool_use", "id": call.get("id") or f"toolu_nuru_{index}_{n}",
                                   "name": fn.get("name", ""), "input": _arguments(fn.get("arguments"))})
            pending.extend(b["id"] for b in blocks if b.get("type") == "tool_use")
            add("assistant", blocks)
        elif role == "tool":
            if pending:
                add("user", [{"type": "tool_result", "tool_use_id": pending.pop(0), "content": content or "(empty)"}])
            elif content.strip():
                add("user", [{"type": "text", "text": content}])
        elif role != "system":
            answer_pending()
            if content.strip():
                add("user", [{"type": "text", "text": content}])
    answer_pending()
    return out


def _tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    converted = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                  "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
                 for t in tools]
    converted[-1] = {**converted[-1], "cache_control": _CACHE}  # caches the system prompt and every tool
    return converted


def _thinking(think) -> tuple[dict, str | None]:
    """Nuru's think value as Anthropic's: off is thinking disabled; otherwise adaptive thinking at an
    effort - "low"/"medium"/"high" as given, True (the model's own full thinking) as high."""
    if think is False or think in ("off", "false"):
        return {"type": "disabled"}, None
    return {"type": "adaptive"}, think if think in ("low", "medium", "high") else "high"


def _log(entry: dict) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"claude-requests-{datetime.now():%Y-%m-%d}.jsonl"
        with path.open("a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **entry}, ensure_ascii=False) + "\n")
    except OSError:
        pass  # the log must never stop a turn


def chat(
    model: str,
    messages: list[dict],
    num_ctx: int = 8192,
    think: bool | str = True,
    tools: list[dict] | None = None,
    fmt: dict | None = None,
    options: dict | None = None,
    system: str | None = None,
):
    """See the module docstring. `num_ctx` is Nuru's own budget (the API has no such setting) and
    `options` (sampling) is ignored: these models reject temperature, top_p and top_k."""
    thinking, effort = _thinking(think)
    output_config = {**({"effort": effort} if effort else {}),
                     **({"format": {"type": "json_schema", "schema": fmt}} if fmt is not None else {})}
    request = {
        "model": model,
        "messages": to_anthropic(messages),
        "system": [{"type": "text", "text": system, "cache_control": _CACHE}] if system else None,
        "max_tokens": MAX_TOKENS,
        "tools": _tools(tools),
        "thinking": thinking,
        "output_config": output_config or None,
    }
    entry = {"model": model, "think": think, "effort": effort, "format": fmt is not None,
             "tools": [t["name"] for t in request["tools"] or []], "system_chars": len(system or ""),
             "messages": request["messages"]}
    started = time.monotonic()
    reasoning, message = "", None
    try:
        for event in provider().stream_message(**request):
            if event["type"] == "text":
                yield event["text"]
            elif event["type"] == "thinking":
                reasoning += event["text"]
                yield {"thinking_chunk": event["text"]}
            elif event["type"] == "message":
                message = event["message"]
    except anthropic.APIError as e:
        _log({**entry, "error": f"{type(e).__name__}: {e}", "duration_ms": round((time.monotonic() - started) * 1000)})
        raise ClaudeError(f"Claude API error ({type(e).__name__}): {e}") from e

    message = message or {}
    content = message.get("content") or []
    calls = [{"id": b["id"], "function": {"name": b["name"], "arguments": b.get("input") or {}}}
             for b in content if b.get("type") == "tool_use"]
    if calls:
        yield {"tool_calls": calls}
        yield {"provider_content": content}
    if reasoning:
        yield {"thinking": reasoning}
    usage = message.get("usage") or {}
    cache_read = usage.get("cache_read_input_tokens") or 0
    cache_write = usage.get("cache_creation_input_tokens") or 0
    stop = message.get("stop_reason")
    result = {
        "prompt_tokens": (usage.get("input_tokens") or 0) + cache_read + cache_write,
        "completion_tokens": usage.get("output_tokens") or 0,
        "stop_reason": _STOP_REASONS.get(stop, stop),
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
    }
    _log({**entry, "usage": usage, "stop_reason": stop, "answered_by": message.get("model"),
          "duration_ms": round((time.monotonic() - started) * 1000)})
    yield result
