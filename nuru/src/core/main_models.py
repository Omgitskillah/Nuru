"""The main models a conversation can run on, for the model and thinking buttons in a client: every
model pulled in Ollama whose family has a supported profile (modelfiles/profiles.yaml), and Claude
Haiku and Sonnet when an API key is set, marked `cloud` since what the model reads then leaves the
machine. A model's thinking levels are the profile's levels that differ (Qwen 3.5's low and high are
the same, so it gets Off and On); none for a model that can't think.

IDs are what NURU_BASE_MODEL / NURU_MAIN_MODEL take: an Ollama tag, or anthropic:<model>.
"""

import time

import requests
from claude_access.keys import api_key
from claude_access.models import MAIN_MODELS

from core import model_profile
from core.providers import claude as claude_provider
from core.providers.ollama import BASE, model_info

LEVELS = ("off", "low", "high")
_LABELS = {"claude-haiku-5-5": "Claude Haiku 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5"}
_CACHE_SECONDS = 30  # clients ask for the status every few seconds; pulling a model is rare
_cache: tuple[float, list[dict]] | None = None


def thinking(profile: model_profile.ModelProfile, can_think: bool) -> tuple[list[dict], str | None]:
    """(the levels to offer, as {"id", "label"}, and the default one), or ([], None)."""
    if not can_think or not profile.think_map:
        return [], None
    distinct: dict = {}  # Ollama think value -> the first level that sends it
    for level in LEVELS:
        distinct.setdefault(profile.think_map[level], level)
    levels = list(distinct.values())
    if len(levels) < 2:
        return [], None
    names = {"off": "Off", "low": "Low", "high": "High"} if len(levels) > 2 else dict(zip(levels, ("Off", "On")))
    default = distinct[profile.think_map[profile.default]]
    return [{"id": level, "label": names[level]} for level in levels], default


def _choice(model_id: str, label: str, cloud: bool, profile, can_think: bool) -> dict:
    levels, default = thinking(profile, can_think)
    return {"id": model_id, "label": label, "cloud": cloud, "thinking": levels, "default_thinking": default}


def _local() -> list[dict]:
    try:
        tags = requests.get(f"{BASE}/api/tags", timeout=5).json().get("models", [])
    except (requests.RequestException, ValueError):
        return []
    found = []
    for name in sorted(m["name"] for m in tags):
        try:
            profile = model_profile.for_model(name)  # unsupported families and Nuru's own aliases fail here
            can_think = "thinking" in model_info(name).get("capabilities", [])
        except (ValueError, requests.RequestException):
            continue
        found.append(_choice(name, name, False, profile, can_think))
    return found


def _cloud() -> list[dict]:
    if not api_key():
        return []
    return [_choice(claude_provider.PREFIX + name, _LABELS.get(name, name), True, model_profile.for_model(name), True)
            for name in MAIN_MODELS]


def choices(default_id: str) -> dict:
    """{"default": default_id, "choices": [...]}: the configured model first, then the rest."""
    global _cache
    if _cache is None or time.monotonic() - _cache[0] > _CACHE_SECONDS:
        _cache = (time.monotonic(), _local() + _cloud())
    listed = _cache[1]
    first = [c for c in listed if c["id"] == default_id]
    return {"default": default_id, "choices": first + [c for c in listed if c["id"] != default_id]}


def find(model_id: str, default_id: str) -> dict | None:
    """The choice for `model_id`, if it's one a conversation may pick."""
    return next((c for c in choices(default_id)["choices"] if c["id"] == model_id), None)
