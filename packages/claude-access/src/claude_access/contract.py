"""The API contract every Claude-access provider implements.

Callers (deep-think's tools, Nuru's main-model provider) program against this, so a provider can be
swapped - or faked in tests - without touching them.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator


class Provider(ABC):
    """One backend implementing the contract."""

    @abstractmethod
    def send_message(
        self,
        model: str,
        messages: list[dict],
        system: str | list[dict] | None = None,
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
        thinking: dict | None = None,
        output_config: dict | None = None,
        fallbacks: str | None = None,
    ) -> dict:
        """Equivalent of POST /v1/messages. Raises ValueError if messages is empty."""

    @abstractmethod
    def stream_message(
        self,
        model: str,
        messages: list[dict],
        system: str | list[dict] | None = None,
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
        thinking: dict | None = None,
        output_config: dict | None = None,
        fallbacks: str | None = None,
    ) -> Iterator[dict]:
        """POST /v1/messages, streamed. Yields {"type": "text", "text": ...} and
        {"type": "thinking", "text": ...} as they arrive, then one {"type": "message", "message": ...}
        with the whole response (content blocks, stop_reason, usage). Closing the iterator early
        closes the request. Raises ValueError if messages is empty."""

    @abstractmethod
    def count_tokens(
        self,
        model: str,
        messages: list[dict],
        system: str | list[dict] | None = None,
        tools: list[dict] | None = None,
    ) -> dict:
        """Equivalent of POST /v1/messages/count_tokens. Raises ValueError if messages is empty."""

    @abstractmethod
    def list_models(self) -> dict:
        """Equivalent of GET /v1/models."""
