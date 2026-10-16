"""Provider registry: picks which backend deep-think's tools dispatch to.

Selected via the DEEP_THINK_PROVIDER env var (defaults to "claude"). The providers live in the shared
claude-access package; add a backend by implementing claude_access.contract.Provider and registering it
in _REGISTRY below.
"""

import os
from functools import lru_cache
from pathlib import Path

from claude_access.claude import ClaudeProvider
from claude_access.contract import Provider
from dotenv import load_dotenv

# deep-think's settings (the provider, the key) live in the repo root's .env.deep-think.
load_dotenv(Path(__file__).resolve().parents[5] / ".env.deep-think")

_REGISTRY: dict[str, type[Provider]] = {
    "claude": ClaudeProvider,
}


@lru_cache(maxsize=1)
def get_provider() -> Provider:
    """Return the selected provider, instantiated once per server process."""
    name = os.environ.get("DEEP_THINK_PROVIDER", "claude").lower()
    try:
        provider_cls = _REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"Unknown DEEP_THINK_PROVIDER '{name}'. Available: {', '.join(sorted(_REGISTRY))}"
        ) from None
    return provider_cls()
