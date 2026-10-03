"""The decision layer's one config file (config/decision_layer.yaml at the repo root).

Loaded and validated as a whole, so a bad value fails at startup rather than on the first
request that happens to read it. Enforce mode is refused here, for any decision point that
uses a threshold not yet calibrated on held-out labelled data.

Imports nothing heavy: Nuru's harness and the labelling CLI read this too.
"""

import os
import datetime as dt
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

REPO_ROOT = Path(__file__).resolve().parents[4]
CONFIG_PATH = Path(os.environ.get("NURU_DECISION_CONFIG", REPO_ROOT / "config" / "decision_layer.yaml"))

# off: not asked at all (the code stays, for later); shadow: asked and logged; enforce: acted on.
Mode = Literal["off", "shadow", "enforce"]
Tier = Literal["off", "low", "high", "expert"]

# Which question sets each decision point asks. Enforcing a point requires every threshold of
# every set it asks to be calibrated - so the points decided by code alone ask none, and can be
# enforced now: step_cap (tool_gate's step limit), write_checks (gate_write's code checks: invented
# note IDs, blocked tools, protected paths) and watch_thinking (the budget, repeated sentences and
# repeated calls). watch_laya is the watch's Laya question, acted on only once it is calibrated.
DECISION_POINT_SETS = {
    "tool_gate": ("tool_gate_start", "tool_gate_next"),
    "step_cap": (),
    "assess_effort": ("assess_effort",),
    "gate_write": ("gate_write",),
    "write_checks": (),
    "check_claim": ("check_claim",),
    "rerank": ("rerank",),
    "watch_thinking": (),
    "watch_laya": ("watch_thinking",),
    "note_type": ("note_type",),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Calibration(_Strict):
    date: dt.date | None = None
    labels: int = Field(0, ge=0)
    held_out: int = Field(0, ge=0)

    @property
    def done(self) -> bool:
        return self.date is not None and self.held_out > 0


class Threshold(_Strict):
    min_p: float = Field(ge=0.0, le=1.0)
    calibration: Calibration = Calibration()


class LayaSettings(_Strict):
    device: Literal["auto", "mps", "cpu"] = "auto"
    checkpoints: tuple[Literal["english", "multilingual", "typed-decisions"], ...] = ("english", "multilingual")
    revision: str | None = None
    offline: bool = True


class ToolGateSettings(_Strict):
    step_cap: int = Field(ge=1)
    default_first_read: str


class TierSettings(_Strict):
    order: tuple[Tier, ...]
    default: Tier
    # How a tier becomes an Ollama `think` value depends on the model: nuru/modelfiles/profiles.yaml.


class ExpertSettings(_Strict):
    model: str
    daily_cap: int = Field(ge=0)


class RerankSettings(_Strict):
    top_k: int = Field(ge=1)


class WatchSettings(_Strict):
    check_every_tokens: int = Field(ge=50)
    budgets: dict[Literal["low", "high"], int]  # thinking tokens per model call, per tier
    repeat_min_chars: int = Field(ge=10)
    repeat_count: int = Field(ge=2)
    tail_chars: int = Field(ge=200)
    repeat_calls: int = Field(ge=2)  # identical tool calls in a turn that make a loop


class DecisionConfig(_Strict):
    database: Path
    laya: LayaSettings
    decision_points: dict[str, Mode]
    tool_gate: ToolGateSettings
    tiers: TierSettings
    expert: ExpertSettings
    rerank: RerankSettings
    watch_thinking: WatchSettings
    # question set -> question -> option -> threshold
    thresholds: dict[str, dict[str, dict[str, Threshold]]]

    @model_validator(mode="after")
    def _check(self) -> "DecisionConfig":
        unknown = set(self.decision_points) - set(DECISION_POINT_SETS)
        missing = set(DECISION_POINT_SETS) - set(self.decision_points)
        if unknown or missing:
            raise ValueError(f"decision_points: unknown {sorted(unknown)}, missing {sorted(missing)}")
        for point, mode in self.decision_points.items():
            if mode != "enforce":
                continue
            uncalibrated = [
                f"{qset}.{question}.{option}"
                for qset in DECISION_POINT_SETS[point]
                for question, options in self.thresholds.get(qset, {}).items()
                for option, threshold in options.items()
                if not threshold.calibration.done
            ]
            if uncalibrated:
                raise ValueError(
                    f"{point} is set to enforce, but these thresholds have not been calibrated on "
                    f"held-out labelled data: {', '.join(uncalibrated)}"
                )
        return self

    def mode(self, point: str) -> Mode:
        return self.decision_points[point]

    def threshold(self, qset: str, question: str, option: str) -> float | None:
        """min_p for this option, falling back to the question's `any`; None if neither is set."""
        options = self.thresholds.get(qset, {}).get(question, {})
        threshold = options.get(option) or options.get("any")
        return threshold.min_p if threshold else None

    @property
    def database_path(self) -> Path:
        return self.database if self.database.is_absolute() else REPO_ROOT / self.database


def load_config(path: Path = CONFIG_PATH) -> DecisionConfig:
    with open(path) as f:
        return DecisionConfig.model_validate(yaml.safe_load(f))
