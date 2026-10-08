"""The API contract every deep-think provider must implement.

deep-think itself is just an enforcer: its MCP tools (see server.py) accept
requests and dispatch them to whichever Provider is selected, and every
Provider must answer with the same shapes regardless of which underlying
API (Claude, OpenAI, Gemini, ...) it's fronting.
"""

from abc import ABC, abstractmethod


class Provider(ABC):
    """One backend (Claude, OpenAI, Gemini, ...) implementing deep-think's API."""

    @abstractmethod
    def send_message(
        self,
        model: str,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
        thinking: dict | None = None,
        output_config: dict | None = None,
        fallbacks: str | None = None,
    ) -> dict:
        """Equivalent of POST /v1/messages. Raises ValueError if messages is empty."""

    @abstractmethod
    def count_tokens(
        self,
        model: str,
        messages: list[dict],
        system: str | None = None,
        tools: list[dict] | None = None,
    ) -> dict:
        """Equivalent of POST /v1/messages/count_tokens. Raises ValueError if messages is empty."""

    @abstractmethod
    def list_models(self) -> dict:
        """Equivalent of GET /v1/models."""

    @abstractmethod
    def upload_file(self, file_path: str, purpose: str = "user_data", expires_in_seconds: int = 3600) -> dict:
        """Equivalent of POST /v1/files. Raises FileNotFoundError if file_path doesn't exist.

        expires_in_seconds: how long until the uploaded file auto-expires. Must be between
        3600 (1 hour) and 7776000 (90 days); defaults to 3600.
        """

    @abstractmethod
    def list_files(self) -> dict:
        """Equivalent of GET /v1/files."""
