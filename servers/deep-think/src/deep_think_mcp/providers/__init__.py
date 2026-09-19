"""Provider registry: picks which backend deep-think's tools dispatch to.

Selected via the DEEP_THINK_PROVIDER env var (defaults to "claude"). Add a
new backend by implementing deep_think_mcp.contract.Provider and registering
it in _REGISTRY below.
"""

import os
from functools import lru_cache

from deep_think_mcp.contract import Provider
from deep_think_mcp.providers.claude import ClaudeProvider

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
