"""The one place Laya runs.

`LayaPredictor` loads the checkpoints once, in the background (so the MCP server answers
`health` - and fails safe - while they load), keeps them loaded for the life of the process, and
puts every call behind one lock: Laya is not thread-safe, and its Router's own lock only covers
loading. Before each call the state is counted with the answering checkpoint's own tokenizer, and
an input that would be cut off is rejected (`OverBudget`) instead - Laya truncates silently.

Nothing else imports `laya` (and with it torch), so the config, the log and the tests stay light.
"""

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from laya_mcp.config import LayaSettings, load_config
from laya_mcp.question_sets import QuestionSet

State = str | dict | list


class Unavailable(Exception):
    """Laya isn't loaded (still loading, or failed to)."""


class OverBudget(Exception):
    """The input would not fit the checkpoint's context, so it was not sent."""

    def __init__(self, tokens: int, limit: int, what: str = "state"):
        super().__init__(f"{what} needs {tokens} tokens, over the {limit}-token budget")
        self.tokens = tokens
        self.limit = limit


@dataclass(frozen=True)
class Prediction:
    answers: dict[str, dict[str, Any]]
    checkpoint: str
    state_tokens: int
    latency_ms: float


class Predictor(Protocol):
    def status(self) -> dict[str, Any]: ...

    def predict(self, qset: QuestionSet, state: State, questions: dict) -> Prediction: ...


class LayaPredictor:
    def __init__(self, settings: LayaSettings):
        self._settings = settings
        self._lock = threading.Lock()
        self._router = None
        self._state = "not started"
        self._error: str | None = None
        self._device: str | None = None

    def start(self) -> None:
        """Load the checkpoints in a background thread."""
        self._state = "loading"
        threading.Thread(target=self._load, daemon=True, name="laya-load").start()

    def _load(self) -> None:
        try:
            if self._settings.offline:
                os.environ["HF_HUB_OFFLINE"] = "1"
            from laya import Router

            device = None if self._settings.device == "auto" else self._settings.device
            router = Router(
                device=device,
                revision=self._settings.revision,
                max_loaded=len(self._settings.checkpoints),
            )
            with self._lock:
                router.preload(list(self._settings.checkpoints))
                self._device = str(router.load(self._settings.checkpoints[0]).device)
                self._router = router
            self._state = "ready"
        except Exception as e:  # the server keeps running, and every decision falls back
            self._error = f"{type(e).__name__}: {e}"
            self._state = "failed"

    def status(self) -> dict[str, Any]:
        import importlib.metadata

        return {
            "state": self._state,
            "error": self._error,
            "device": self._device,
            "loaded": list(self._router.loaded) if self._router else [],
            "revision": self._settings.revision,
            "laya_version": importlib.metadata.version("laya"),
        }

    def predict(self, qset: QuestionSet, state: State, questions: dict) -> Prediction:
        if self._router is None:
            raise Unavailable(f"Laya is {self._state}" + (f" ({self._error})" if self._error else ""))
        with self._lock:
            # The Router picks multilingual for non-English text; an English set can't be forced
            # onto the English checkpoint, which collapses on other scripts.
            checkpoint = qset.checkpoint
            if checkpoint == "english":
                checkpoint = self._router.route(state, questions)["model"]
            agent = self._router.load(checkpoint)
            state_tokens = self._check_budget(agent, qset, state, questions)
            captured: dict[str, float] = {}
            started = time.perf_counter()
            result = self._router.predict(
                state,
                questions,
                model=checkpoint,
                max_len=qset.max_len,
                head_max_len=qset.head_max_len,
                on_predict_end=lambda ctx: captured.update(elapsed_ms=ctx.elapsed_ms or 0.0),
            )
            latency = captured.get("elapsed_ms") or (time.perf_counter() - started) * 1000
        return Prediction(result["answers"], checkpoint, state_tokens, latency)

    @staticmethod
    def _check_budget(agent, qset: QuestionSet, state: State, questions: dict) -> int:
        """Count the state with this checkpoint's tokenizer; raise OverBudget rather than let Laya
        cut it off. Exact: the question rows are built the way Laya builds them, with no state."""
        from laya.agent import Agent
        from laya.common import build_sequence, encode_text, serialize_state

        tok = agent.tok
        state_tokens = len(
            encode_text(tok, serialize_state(state).replace(tok.mask_token, " "), add_special_tokens=False)["input_ids"]
        )
        if state_tokens > qset.max_state_tokens:
            raise OverBudget(state_tokens, qset.max_state_tokens)
        limit = qset.max_len or agent.cfg.get("max_len", 512)
        head_max_len = qset.head_max_len or agent.cfg.get("head_max_len", 192)
        for qid, qdef in questions.items():
            internal = Agent._to_internal(qdef)
            empty, _, stats = build_sequence(tok, "", internal, 10**9, head_max_len, return_stats=True)
            if stats["options_distinct"] < stats["options"]:
                raise OverBudget(stats["options"], stats["options_distinct"], f"{qid}'s options (collapsed)")
            if len(empty) + state_tokens > limit:
                raise OverBudget(len(empty) + state_tokens, limit, f"{qid} with this state")
        return state_tokens


def fetch() -> None:
    """`uv run laya-mcp-fetch`: download the pinned checkpoints once, so the server can run offline."""
    settings = load_config().laya
    os.environ.pop("HF_HUB_OFFLINE", None)
    from laya import Router

    Router(revision=settings.revision, max_loaded=len(settings.checkpoints)).preload(list(settings.checkpoints))
    print(f"Fetched {', '.join(settings.checkpoints)} at revision {settings.revision}.")
