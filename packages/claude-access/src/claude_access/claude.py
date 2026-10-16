"""Claude provider: the contract against the real Claude API, through the official `anthropic` SDK -
POST /v1/messages (plain and streamed), /v1/messages/count_tokens and GET /v1/models.

The key comes from keys.api_key(); without one, the SDK's own credential resolution applies.
"""

import functools
from collections.abc import Iterator

import anthropic

from claude_access.contract import Provider
from claude_access.keys import api_key

_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def build_client(key: str | None = None) -> anthropic.Anthropic:
    key = key or api_key()
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


def _page_to_dict(page) -> dict:
    """Flatten a paginated SDK response (models.list) to a plain dict.

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


def _request(model, messages, system, max_tokens, tools, thinking, output_config) -> dict:
    if not messages:
        raise ValueError("messages must not be empty")
    return {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        **({"system": system} if system else {}),
        **({"tools": tools} if tools else {}),
        **({"thinking": thinking} if thinking else {}),
        **({"output_config": output_config} if output_config else {}),
    }


class ClaudeProvider(Provider):
    """Talks to the Claude Messages and Models API."""

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self._client = client or build_client()

    def _endpoint(self, fallbacks: str | None):
        # Server-side refusal fallback ("default": the API picks the substitute by refusal category)
        # is a beta, so it goes through the beta endpoint with its header.
        if fallbacks:
            return self._client.beta.messages, {"betas": [_FALLBACK_BETA], "fallbacks": fallbacks}
        return self._client.messages, {}

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
        request = _request(model, messages, system, max_tokens, tools, thinking, output_config)
        endpoint, extra = self._endpoint(fallbacks)
        create = functools.partial(endpoint.create, **extra) if extra else endpoint.create
        return create(**request).to_dict()

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
        request = _request(model, messages, system, max_tokens, tools, thinking, output_config)
        endpoint, extra = self._endpoint(fallbacks)
        with endpoint.stream(**request, **extra) as stream:
            for event in stream:
                if event.type == "text" and event.text:
                    yield {"type": "text", "text": event.text}
                elif event.type == "thinking" and event.thinking:
                    yield {"type": "thinking", "text": event.thinking}
            yield {"type": "message", "message": stream.get_final_message().to_dict()}

    def count_tokens(
        self,
        model: str,
        messages: list[dict],
        system: str | list[dict] | None = None,
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
