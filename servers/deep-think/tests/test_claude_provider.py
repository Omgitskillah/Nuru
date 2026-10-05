from unittest.mock import MagicMock

import pytest

from deep_think_mcp.providers.claude import ClaudeProvider
from pathlib import Path
from deep_think_mcp.providers import claude


def _fake_response(data: dict) -> MagicMock:
    """A stand-in for an SDK response object whose .to_dict() returns data."""
    response = MagicMock()
    response.to_dict.return_value = data
    return response


@pytest.fixture
def client():
    """A fake anthropic.Anthropic client - no real network call ever happens."""
    return MagicMock()


@pytest.fixture
def provider(client):
    return ClaudeProvider(client=client)


# --- send_message ---


def test_send_message_calls_the_client_and_returns_its_response(provider, client):
    client.messages.create.return_value = _fake_response({"role": "assistant", "content": []})

    response = provider.send_message(model="claude-opus-5", messages=[{"role": "user", "content": "hi"}])

    client.messages.create.assert_called_once_with(
        model="claude-opus-5", max_tokens=1024, messages=[{"role": "user", "content": "hi"}]
    )
    assert response == {"role": "assistant", "content": []}


def test_send_message_forwards_system_prompt(provider, client):
    client.messages.create.return_value = _fake_response({})

    provider.send_message(model="claude-opus-5", messages=[{"role": "user", "content": "hi"}], system="be terse")

    client.messages.create.assert_called_once_with(
        model="claude-opus-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": "hi"}],
        system="be terse",
    )


def test_send_message_forwards_tools(provider, client):
    client.messages.create.return_value = _fake_response({})
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]

    provider.send_message(model="claude-opus-5", messages=[{"role": "user", "content": "hi"}], tools=tools)

    client.messages.create.assert_called_once_with(
        model="claude-opus-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": "hi"}],
        tools=tools,
    )


def test_send_message_raises_on_empty_messages_without_calling_client(provider, client):
    with pytest.raises(ValueError):
        provider.send_message(model="claude-opus-5", messages=[])

    client.messages.create.assert_not_called()


# --- count_tokens ---


def test_count_tokens_calls_the_client_and_returns_its_response(provider, client):
    client.messages.count_tokens.return_value = _fake_response({"input_tokens": 42})

    response = provider.count_tokens(model="claude-opus-5", messages=[{"role": "user", "content": "hi"}])

    client.messages.count_tokens.assert_called_once_with(
        model="claude-opus-5", messages=[{"role": "user", "content": "hi"}]
    )
    assert response == {"input_tokens": 42}


def test_count_tokens_raises_on_empty_messages_without_calling_client(provider, client):
    with pytest.raises(ValueError):
        provider.count_tokens(model="claude-opus-5", messages=[])

    client.messages.count_tokens.assert_not_called()


# --- list_models ---


def test_list_models_flattens_the_paginated_response(provider, client):
    page = MagicMock()
    page.data = [_fake_response({"id": "claude-opus-5"}), _fake_response({"id": "claude-sonnet-5"})]
    page.has_more = False
    page.first_id = "claude-opus-5"
    page.last_id = "claude-sonnet-5"
    client.models.list.return_value = page

    response = provider.list_models()

    assert response == {
        "data": [{"id": "claude-opus-5"}, {"id": "claude-sonnet-5"}],
        "has_more": False,
        "first_id": "claude-opus-5",
        "last_id": "claude-sonnet-5",
    }


# --- upload_file / list_files ---


def test_upload_file_sends_the_file_and_returns_the_client_response(provider, client, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("hello world")
    client.files.upload.return_value = _fake_response({"id": "file_abc", "filename": "notes.txt"})

    response = provider.upload_file(str(target))

    assert response == {"id": "file_abc", "filename": "notes.txt"}
    _, kwargs = client.files.upload.call_args
    filename, _file_obj, mime_type = kwargs["file"]
    assert filename == "notes.txt"
    assert mime_type == "text/plain"


def test_upload_file_defaults_to_a_one_hour_expiration(provider, client, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("hello world")
    client.files.upload.return_value = _fake_response({"id": "file_abc"})

    provider.upload_file(str(target))

    _, kwargs = client.files.upload.call_args
    assert kwargs["expires_in_seconds"] == 3600


def test_upload_file_forwards_a_custom_expiration(provider, client, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("hello world")
    client.files.upload.return_value = _fake_response({"id": "file_abc"})

    provider.upload_file(str(target), expires_in_seconds=7200)

    _, kwargs = client.files.upload.call_args
    assert kwargs["expires_in_seconds"] == 7200


def test_upload_file_raises_on_missing_file_without_calling_client(provider, client, tmp_path):
    with pytest.raises(FileNotFoundError):
        provider.upload_file(str(tmp_path / "does-not-exist.txt"))

    client.files.upload.assert_not_called()


def test_list_files_flattens_the_paginated_response(provider, client):
    page = MagicMock()
    page.data = [_fake_response({"id": "file_abc"})]
    page.has_more = False
    page.first_id = "file_abc"
    page.last_id = "file_abc"
    client.files.list.return_value = page

    response = provider.list_files()

    assert response["data"] == [{"id": "file_abc"}]


# --- _upload_mime_type ---


@pytest.mark.parametrize(
    "name,expected",
    [
        ("battery-life-modeling.md", "text/plain"),   # a document block rejects text/markdown
        ("notes.txt", "text/plain"),
        ("server.py", "text/plain"),
        ("datasheet.pdf", "application/pdf"),
        ("diagram.png", "image/png"),
        ("mystery.bin", "application/octet-stream"),
    ],
)
def test_upload_mime_type_sends_text_formats_as_plain_text(name, expected):
    assert claude._upload_mime_type(Path(name)) == expected


def test_send_message_forwards_thinking_and_output_config(provider, client):
    client.messages.create.return_value = _fake_response({})
    thinking = {"type": "enabled", "budget_tokens": 1024}
    output_config = {"format": {"type": "json_schema", "schema": {"type": "object"}}}

    provider.send_message(
        model="claude-haiku-4-5", messages=[{"role": "user", "content": "hi"}], thinking=thinking,
        output_config=output_config,
    )

    client.messages.create.assert_called_once_with(
        model="claude-haiku-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": "hi"}],
        thinking=thinking,
        output_config=output_config,
    )


def test_send_message_with_fallbacks_uses_the_beta_endpoint(provider, client):
    client.beta.messages.create.return_value = _fake_response({})

    provider.send_message(model="claude-sonnet-5-5", messages=[{"role": "user", "content": "hi"}], fallbacks="default")

    client.beta.messages.create.assert_called_once_with(
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        model="claude-sonnet-5-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": "hi"}],
    )
    client.messages.create.assert_not_called()
