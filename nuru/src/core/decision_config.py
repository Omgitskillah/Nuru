"""Validated settings for deterministic safeguards, tiers and the local decision log."""

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = Path(
    os.environ.get("NURU_DECISION_CONFIG", REPO_ROOT / "config" / "decision_layer.yaml")
)

# off: not asked at all (the code stays, for later); shadow: asked and logged; enforce: acted on.
Mode = Literal["off", "shadow", "enforce"]
Tier = Literal["off", "low", "high", "expert"]

# Deterministic safeguards; no model or calibrated thresholds are involved.
DECISION_POINT_SETS = {"step_cap": (), "write_checks": (), "watch_thinking": ()}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolGateSettings(_Strict):
    step_cap: int = Field(ge=1)


class TierSettings(_Strict):
    order: tuple[Tier, ...]
    default: Tier
    enforce: bool = (
        False  # optional deterministic escalation, independent of a learned classifier
    )
    # How a tier becomes an Ollama `think` value depends on the model: nuru/modelfiles/profiles.yaml.


class ExpertSettings(_Strict):
    model: str
    daily_cap: int = Field(ge=0)
    # Whether a turn at the step cap asks the expert for a plan first. Either way, a turn the expert
    # doesn't rescue ends with one last model call without tools: an answer from what it has.
    at_step_cap: bool = True


class WatchSettings(_Strict):
    check_every_tokens: int = Field(ge=50)
    budgets: dict[
        Literal["low", "high"], int
    ]  # thinking tokens per model call, per tier
    repeat_min_chars: int = Field(ge=10)
    repeat_count: int = Field(ge=2)
    repeat_calls: int = Field(ge=2)  # identical tool calls in a turn that make a loop


class DecisionConfig(_Strict):
    database: Path
    decision_points: dict[str, Mode]
    tool_gate: ToolGateSettings
    tiers: TierSettings
    expert: ExpertSettings
    watch_thinking: WatchSettings

    @model_validator(mode="after")
    def _check(self) -> "DecisionConfig":
        unknown = set(self.decision_points) - set(DECISION_POINT_SETS)
        missing = set(DECISION_POINT_SETS) - set(self.decision_points)
        if unknown or missing:
            raise ValueError(
                f"decision_points: unknown {sorted(unknown)}, missing {sorted(missing)}"
            )
        return self

    def mode(self, point: str) -> Mode:
        return self.decision_points.get(point, "off")

    @property
    def database_path(self) -> Path:
        return (
            self.database if self.database.is_absolute() else REPO_ROOT / self.database
        )


def load_config(path: Path = CONFIG_PATH) -> DecisionConfig:
    with open(path, encoding="utf-8") as f:
        return DecisionConfig.model_validate(yaml.safe_load(f))
