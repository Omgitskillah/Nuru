from unittest.mock import MagicMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from deep_think_mcp import server
from deep_think_mcp.contract import Provider
from deep_think_mcp.providers import get_provider
from deep_think_mcp.providers.claude import ClaudeProvider


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    get_provider.cache_clear()
    yield
    get_provider.cache_clear()


def test_default_provider_is_claude(monkeypatch):
    monkeypatch.delenv("DEEP_THINK_PROVIDER", raising=False)

    assert isinstance(get_provider(), ClaudeProvider)


def test_provider_selected_via_env_var(monkeypatch):
    monkeypatch.setenv("DEEP_THINK_PROVIDER", "claude")

    assert isinstance(get_provider(), ClaudeProvider)


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("DEEP_THINK_PROVIDER", "does-not-exist")

    with pytest.raises(ValueError, match="Unknown DEEP_THINK_PROVIDER"):
        get_provider()


def test_ask_an_expert_sends_the_message_with_no_files(monkeypatch):
    fake_provider = MagicMock(spec=Provider)
    fake_provider.send_message.return_value = {"role": "assistant", "content": []}
    monkeypatch.setattr(server, "get_provider", lambda: fake_provider)

    response = server.ask_an_expert(message="what is this?")

    fake_provider.send_message.assert_called_once_with(
        model=server.ASK_AN_EXPERT_MODEL,
        messages=[{"role": "user", "content": [{"type": "text", "text": "what is this?"}]}],
    )
    assert response == {"role": "assistant", "content": []}


def test_ask_an_expert_attaches_uploaded_files(monkeypatch, tmp_path):
    doc = tmp_path / "notes.txt"
    doc.write_text("hello")
    image = tmp_path / "photo.png"
    image.write_bytes(b"\x89PNG\r\n")

    fake_provider = MagicMock(spec=Provider)
    fake_provider.upload_file.side_effect = [
        {"id": "file_doc", "expires_at": None},
        {"id": "file_img", "expires_at": None},
    ]
    fake_provider.send_message.return_value = {"role": "assistant", "content": []}
    monkeypatch.setattr(server, "get_provider", lambda: fake_provider)
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "cache.db")

    server.ask_an_expert(message="summarize these", file_paths=[str(doc), str(image)])

    _, kwargs = fake_provider.send_message.call_args
    assert kwargs["messages"][0]["content"] == [
        {"type": "text", "text": "summarize these"},
        {"type": "document", "source": {"type": "file", "file_id": "file_doc"}},
        {"type": "image", "source": {"type": "file", "file_id": "file_img"}},
    ]


def test_ask_an_expert_missing_file_becomes_tool_error(tmp_path):
    with pytest.raises(ToolError):
        server.ask_an_expert(message="hi", file_paths=[str(tmp_path / "does-not-exist.txt")])
