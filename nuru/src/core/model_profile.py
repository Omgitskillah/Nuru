"""The settings that go with a model family besides its Modelfile - how Nuru's thinking levels
translate to Ollama's `think`, the level to run at, and sampling that depends on whether the model
thinks - read from modelfiles/profiles.yaml, so switching NURU_BASE_MODEL switches them too.
"""

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROFILES = Path(__file__).resolve().parents[2] / "modelfiles" / "profiles.yaml"
LEVELS = ("off", "low", "high")
_SAMPLING_OPTIONS = {"temperature", "top_p", "top_k", "min_p", "presence_penalty", "repeat_penalty"}


def family(model: str) -> str:
    """A model's family: its name with any registry namespace and `:tag` stripped, so `qwen3.5:9b`,
    `qwen3.5` and `library/qwen3.5:latest` are all `qwen3.5`."""
    return model.split("/")[-1].split(":")[0]


@dataclass(frozen=True)
class ModelProfile:
    think_map: dict[str, bool | str] | None = None  # level -> Ollama `think`; None: no translation
    default: str = "low"
    sampling: dict[str, dict] = field(default_factory=dict)  # "thinking" / "no_thinking" -> options

    def think(self, level: str | bool) -> bool | str:
        """Translate a level; a configured thinking map rejects unknown levels."""
        if isinstance(level, bool):
            return level
        level = level.strip().lower()
        if self.think_map is not None and level in self.think_map:
            return self.think_map[level]
        if level in ("off", "false"):
            return False
        if level == "true":
            return True
        if self.think_map is not None:
            raise ValueError(f"Unsupported thinking level {level!r}; use one of {LEVELS}")
        return level

    def options(self, think: bool | str) -> dict:
        """Keyword arguments for chat(): the sampling for thinking on or off, or none at all, so a
        family without sampling here keeps its Modelfile's."""
        chosen = self.sampling.get("thinking" if think else "no_thinking")
        return {"options": dict(chosen)} if chosen else {}


def for_model(model: str, path: Path = PROFILES) -> ModelProfile:
    profiles = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(profiles, dict):
        raise ValueError(f"{path}: expected a mapping of model families")
    entry = profiles.get(family(model))
    if entry is None:
        raise ValueError(f"{path}: no explicit profile for model {model!r}")
    if not isinstance(entry, dict) or type(entry.get("supported")) is not bool:
        raise ValueError(f"{path}: {family(model)} needs an explicit supported: true/false")
    if not entry["supported"]:
        raise ValueError(f"Unsupported model family {family(model)!r}: {entry.get('reason') or 'not validated'}")
    unknown = set(entry) - {"supported", "think", "default", "sampling"}
    if unknown:
        raise ValueError(f"{path}: {family(model)} has unknown profile fields: {sorted(unknown)}")
    think_map = entry.get("think")
    if not isinstance(think_map, dict) or set(think_map) != set(LEVELS):
        raise ValueError(f"{path}: {family(model)}'s think needs exactly {LEVELS}")
    if any(type(value) is not bool and value not in ("low", "high") for value in think_map.values()):
        raise ValueError(f"{path}: {family(model)}'s think values must be booleans, low or high")
    default = entry.get("default")
    if not isinstance(default, str) or default not in LEVELS:
        raise ValueError(f"{path}: {family(model)}'s default must be one of {LEVELS}, got {default!r}")
    sampling = entry.get("sampling", {})
    if not isinstance(sampling, dict) or set(sampling) - {"thinking", "no_thinking"}:
        raise ValueError(f"{path}: {family(model)}'s sampling needs thinking/no_thinking mappings")
    for mode, options in sampling.items():
        if not isinstance(options, dict) or set(options) - _SAMPLING_OPTIONS:
            raise ValueError(f"{path}: {family(model)} has invalid sampling options for {mode}")
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in options.values()):
            raise ValueError(f"{path}: {family(model)}'s sampling values must be finite numbers")
        if "top_k" in options and (type(options["top_k"]) is not int or options["top_k"] < 0):
            raise ValueError(f"{path}: {family(model)}'s top_k must be a nonnegative integer")
    return ModelProfile(think_map, default, sampling)


def for_runtime_model(model: str, *, derived_model: str, base_model: str, path: Path = PROFILES) -> ModelProfile:
    """The rebuilt alias inherits its base profile; an independent planner needs its own."""
    def tagged(name: str) -> str:
        return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"

    return for_model(base_model if tagged(model) == tagged(derived_model) else model, path)
