"""Claude provider: implements deep-think's contract against the real Claude API.

Talks to Anthropic over the network via the official `anthropic` SDK -
POST /v1/messages, /v1/messages/count_tokens, GET /v1/models, and the Files
API. Auth comes from the CLAUDE_API_KEY env var (see .env.deep-think / .env.deep-think.example in the repo root);
if unset, falls back to the SDK's own credential resolution
(ANTHROPIC_API_KEY, an `ant auth login` profile, etc).
"""

import mimetypes
import os
from pathlib import Path

import functools

import anthropic
from dotenv import load_dotenv

from deep_think_mcp.contract import Provider

# deep-think's settings (the key, the base dir) live in the repo root's .env.deep-think.
load_dotenv(Path(__file__).resolve().parents[5] / ".env.deep-think")


_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _build_client() -> anthropic.Anthropic:
    api_key = os.environ.get("CLAUDE_API_KEY")
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def _upload_mime_type(path: Path) -> str:
    """The media type to upload `path` under.

    A document content block accepts only PDF and text/plain, so every other text
    format - text/markdown for a vault note, text/x-python for a source file - has to
    go up as plain text or the Messages API rejects the whole request with a 400.
    """
    guessed = mimetypes.guess_type(path.name)[0]
    if guessed is None:
        return "application/octet-stream"
    if guessed.startswith("text/") and guessed != "text/plain":
        return "text/plain"
    return guessed


def _page_to_dict(page) -> dict:
    """Flatten a paginated SDK response (models.list, files.list) to a plain dict.

    Only the current page's items are returned - callers that need every
    page should iterate the SDK response directly instead of going through
    this contract method.
    """
    return {
        "data": [item.to_dict() for item in page.data],
        "has_more": page.has_more,
        "first_id": getattr(page, "first_id", None),
        "last_id": getattr(page, "last_id", None),
    }


class ClaudeProvider(Provider):
    """Talks to the Claude Messages/Models/Files API."""

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self._client = client or _build_client()

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
        if not messages:
            raise ValueError("messages must not be empty")

        # Server-side refusal fallback ("default": the API picks the substitute by refusal category)
        # is a beta, so it goes through the beta endpoint with its header.
        create = (functools.partial(self._client.beta.messages.create, betas=[_FALLBACK_BETA], fallbacks=fallbacks)
                  if fallbacks else self._client.messages.create)
        response = create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
            **({"system": system} if system else {}),
            **({"tools": tools} if tools else {}),
            **({"thinking": thinking} if thinking else {}),
            **({"output_config": output_config} if output_config else {}),
        )
        return response.to_dict()

    def count_tokens(
        self,
        model: str,
        messages: list[dict],
        system: str | None = None,
        tools: list[dict] | None = None,
    ) -> dict:
        if not messages:
            raise ValueError("messages must not be empty")

        response = self._client.messages.count_tokens(
            model=model,
            messages=messages,
            **({"system": system} if system else {}),
            **({"tools": tools} if tools else {}),
        )
        return response.to_dict()

    def list_models(self) -> dict:
        return _page_to_dict(self._client.models.list())

    def upload_file(self, file_path: str, purpose: str = "user_data", expires_in_seconds: int = 3600) -> dict:
        # purpose is part of deep-think's contract (mirroring OpenAI's Files
        # API), but Claude's own Files API has no such field - accepted for
        # signature compatibility, never sent to Anthropic.
        path = Path(file_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"No such file: '{file_path}'")

        mime_type = _upload_mime_type(path)
        with path.open("rb") as f:
            uploaded = self._client.files.upload(
                file=(path.name, f, mime_type), expires_in_seconds=expires_in_seconds
            )
        return uploaded.to_dict()

    def list_files(self) -> dict:
        return _page_to_dict(self._client.files.list())
