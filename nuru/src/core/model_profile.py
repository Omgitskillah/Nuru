"""The settings that go with a model family besides its Modelfile - how Nuru's thinking levels
translate to Ollama's `think`, the level to run at, and sampling that depends on whether the model
thinks - read from modelfiles/profiles.yaml, so switching NURU_BASE_MODEL switches them too.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROFILES = Path(__file__).resolve().parents[2] / "modelfiles" / "profiles.yaml"
LEVELS = ("off", "low", "high")


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
        """The Ollama `think` value for a thinking level. Without a map, or for anything that isn't
        a level (an old true/false, "medium"), the value goes through as before."""
        if isinstance(level, bool):
            return level
        level = level.strip().lower()
        if self.think_map is not None and level in self.think_map:
            return self.think_map[level]
        if level in ("off", "false"):
            return False
        if level == "true":
            return True
        return level

    def options(self, think: bool | str) -> dict:
        """Keyword arguments for chat(): the sampling for thinking on or off, or none at all, so a
        family without sampling here keeps its Modelfile's."""
        chosen = self.sampling.get("thinking" if think else "no_thinking")
        return {"options": dict(chosen)} if chosen else {}


def for_model(model: str, path: Path = PROFILES) -> ModelProfile:
    entry = (yaml.safe_load(path.read_text()) or {}).get(family(model)) if path.exists() else None
    if not entry:
        return ModelProfile()
    think_map = entry.get("think")
    if think_map is not None and set(think_map) != set(LEVELS):
        raise ValueError(f"{path}: {family(model)}'s think needs exactly {LEVELS}, got {sorted(think_map)}")
    default = entry.get("default", "low")
    if default not in LEVELS:
        raise ValueError(f"{path}: {family(model)}'s default must be one of {LEVELS}, got {default!r}")
    return ModelProfile(think_map, default, entry.get("sampling") or {})
