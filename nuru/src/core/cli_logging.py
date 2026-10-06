"""JSON-lines logging for CLI sessions.

Mirrors the JSON Lines convention used by the server-side access-logging
middleware, but scoped to what the CLI orchestrates: one file per CLI run
under `log_dir`, one JSON object per line.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PREVIEW_LIMIT = 64


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _preview(text: str, limit: int = _PREVIEW_LIMIT) -> dict[str, Any]:
    truncated = len(text) > limit
    return {"text": text[:limit] + "…" if truncated else text, "truncated": truncated}


class CLILogger:
    """Logs one CLI session as JSON lines to its own file under `log_dir`."""

    def __init__(self, log_dir: Path, model: str, think: bool, server_command: str) -> None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = uuid.uuid4().hex[:8]

        # A uuid, not id(self): see access_logging.middleware for why.
        self._logger = logging.getLogger(f"cli_logging.{uuid.uuid4().hex}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            handler: logging.Handler = logging.FileHandler(log_dir / f"{timestamp}_{self.session_id}.jsonl")
            handler.setFormatter(logging.Formatter("%(message)s"))
        except OSError:
            handler = logging.NullHandler()
        self._logger.addHandler(handler)

        self._model_lifecycle_logged = False
        self._turn_count = 0
        self._tokens_processed = 0
        self._context_tokens = 0

        self._log(
            {
                "event": "session_start",
                "model": model,
                "think": think,
                "server_command": server_command,
            }
        )

    def _log(self, fields: dict[str, Any]) -> None:
        entry = {"ts": _now_iso(), "session_id": self.session_id, **fields}
        try:
            self._logger.info(json.dumps(entry, default=str))
        except Exception:
            pass

    def turn_start(self, text: str) -> str:
        self._turn_count += 1
        turn_id = f"t-{self._turn_count}"
        self._log({"event": "turn_start", "turn_id": turn_id, "input_preview": _preview(text)})
        return turn_id

    def turn_end(self, turn_id: str) -> None:
        self._log({"event": "turn_end", "turn_id": turn_id})

    def turn_error(self, turn_id: str, error: str) -> None:
        self._log({"event": "turn_error", "turn_id": turn_id, "error": error})

    def context_reduction(self, num_ctx: int, history_messages: int, omitted_result_tokens: list[int]) -> None:
        self._log({"event": "context_reduction", "turn_id": f"t-{self._turn_count}",
                   "num_ctx": num_ctx, "history_messages": history_messages,
                   "omitted_result_tokens": omitted_result_tokens})

    def model_lifecycle(self, server_spawned: bool, model_info: dict[str, Any] | None) -> None:
        """Logs the model/server startup info once per session, however many
        times ensure_server_running/ensure_model_loaded get re-checked."""
        if self._model_lifecycle_logged:
            return
        self._model_lifecycle_logged = True
        model_info = model_info or {}
        self._log(
            {
                "event": "model_lifecycle",
                "action": "model_loaded",
                "server_spawned": bool(server_spawned),
                "parameter_size": model_info.get("parameter_size"),
                "quantization_level": model_info.get("quantization_level"),
                "family": model_info.get("family"),
                "supports_thinking": model_info.get("supports_thinking"),
                "context_window": model_info.get("context_window"),
                "embedding_length": model_info.get("embedding_length"),
            }
        )

    def model_call(
        self,
        turn_id: str,
        duration_ms: float,
        prompt_tokens: int,
        completion_tokens: int,
        stop_reason: str | None,
        tool_calls_requested: list[str],
        output_text: str,
    ) -> None:
        self._tokens_processed += prompt_tokens + completion_tokens
        self._context_tokens = prompt_tokens + completion_tokens
        self._log(
            {
                "event": "model_call",
                "turn_id": turn_id,
                "duration_ms": round(duration_ms, 3),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "stop_reason": stop_reason,
                "session_tokens_processed": self._tokens_processed,
                "session_context_tokens": self._context_tokens,
                "tool_calls_requested": tool_calls_requested,
                "output_preview": _preview(output_text),
            }
        )

    def tool_call(
        self,
        turn_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        status: str,
        error: str | None,
        duration_ms: float,
    ) -> None:
        self._log(
            {
                "event": "tool_call",
                "turn_id": turn_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "status": status,
                "error": error,
                "duration_ms": round(duration_ms, 3),
            }
        )

    def index_pass(self, embedded: list[str], moved: list[tuple[str, str]], removed: list[str]) -> None:
        """A background indexing pass that changed the semantic index. Called from the indexer's
        own thread; logging's handlers are thread-safe."""
        self._log(
            {
                "event": "index_pass",
                "embedded": embedded,
                "moved": [{"from": a, "to": b} for a, b in moved],
                "removed": removed,
            }
        )

    def session_end(self, reason: str, models_unloaded: bool, server_stopped: bool) -> None:
        self._log(
            {
                "event": "session_end",
                "reason": reason,
                "models_unloaded": models_unloaded,
                "server_stopped": server_stopped,
                "session_tokens_processed": self._tokens_processed,
                "session_context_tokens": self._context_tokens,
            }
        )
