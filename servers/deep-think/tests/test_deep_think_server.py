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


def test_ask_an_expert_sends_the_question_with_no_files(monkeypatch):
    fake_provider = MagicMock(spec=Provider)
    fake_provider.send_message.return_value = {"role": "assistant", "content": []}
    monkeypatch.setattr(server, "get_provider", lambda: fake_provider)

    response = server.ask_an_expert(question="what is this?")

    fake_provider.send_message.assert_called_once_with(
        model=server.ASK_AN_EXPERT_MODEL,
        messages=[{"role": "user", "content": [{"type": "text", "text": "## The assistant's question\nwhat is this?"}]}],
        system=server.EXPERT_SYSTEM_PROMPT,
        max_tokens=server.ASK_AN_EXPERT_MAX_TOKENS,
    )
    assert response == {
        "answer": "",
        "stop_reason": None,
        "hint": "The expert returned no answer text. Do not repeat the same call; tell the user the expert "
        "did not answer, or ask a narrower question.",
    }


def test_ask_an_expert_returns_only_the_answer_text_not_the_thinking_or_metadata(monkeypatch):
    """The whole response once came to ~12k tokens - mostly the expert's thinking and its
    signature - and pushed a 16k-context caller over its window."""
    fake_provider = MagicMock(spec=Provider)
    fake_provider.send_message.return_value = {
        "id": "msg_1",
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "long reasoning " * 500, "signature": "x" * 20_000},
            {"type": "text", "text": "Yes, it holds."},
            {"type": "text", "text": "The biggest risk is PSM."},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 9000, "output_tokens": 6000},
    }
    monkeypatch.setattr(server, "get_provider", lambda: fake_provider)

    response = server.ask_an_expert(question="audit this")

    assert response == {"answer": "Yes, it holds.\n\nThe biggest risk is PSM.", "stop_reason": "end_turn"}


def test_ask_an_expert_says_when_the_answer_was_cut_off(monkeypatch):
    fake_provider = MagicMock(spec=Provider)
    fake_provider.send_message.return_value = {
        "content": [{"type": "text", "text": "Partial answ"}],
        "stop_reason": "max_tokens",
    }
    monkeypatch.setattr(server, "get_provider", lambda: fake_provider)

    response = server.ask_an_expert(question="q")

    assert response["answer"] == "Partial answ"
    assert "cut off" in response["hint"]


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

    server.ask_an_expert(question="summarize these", file_paths=[str(doc), str(image)])

    _, kwargs = fake_provider.send_message.call_args
    assert kwargs["messages"][0]["content"] == [
        {"type": "document", "source": {"type": "file", "file_id": "file_doc"}},
        {"type": "image", "source": {"type": "file", "file_id": "file_img"}},
        {"type": "text", "text": f"## Attached files\n- {doc}\n- {image}\n\n## The assistant's question\nsummarize these"},
    ]


def test_ask_an_expert_labels_the_request_the_files_the_best_guess_and_the_question_in_that_order(monkeypatch):
    monkeypatch.setattr(server, "_file_content_block", lambda provider, path: {"type": "document"})
    provider = MagicMock(spec=Provider)
    provider.send_message.return_value = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
    monkeypatch.setattr(server, "get_provider", lambda: provider)

    server.ask_an_expert(
        question="Is my verdict right?",
        best_guess="PSM is the biggest risk.",
        file_paths=["nuru/battery.md"],
        user_request="Audit my battery notes.",
    )

    text = provider.send_message.call_args.kwargs["messages"][0]["content"][-1]["text"]
    assert text == (
        "## The user's request (verbatim)\nAudit my battery notes.\n\n"
        "## Attached files\n- nuru/battery.md\n\n"
        "## The assistant's best guess\nPSM is the biggest risk.\n\n"
        "## The assistant's question\nIs my verdict right?"
    )


def test_ask_an_expert_tells_the_expert_who_it_answers_and_to_keep_it_short(monkeypatch):
    provider = MagicMock(spec=Provider)
    provider.send_message.return_value = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
    monkeypatch.setattr(server, "get_provider", lambda: provider)

    server.ask_an_expert(question="q")

    system = provider.send_message.call_args.kwargs["system"]
    assert "relay your answer" in system
    assert "cannot see the vault" in system
    assert "250 words" in system


@pytest.mark.parametrize("question", ["", "   "])
def test_ask_an_expert_needs_a_question(monkeypatch, question):
    provider = MagicMock(spec=Provider)
    monkeypatch.setattr(server, "get_provider", lambda: provider)

    with pytest.raises(ToolError, match="question is empty"):
        server.ask_an_expert(question=question, best_guess="my draft")
    provider.send_message.assert_not_called()


def test_ask_an_expert_missing_file_becomes_tool_error(tmp_path):
    with pytest.raises(ToolError):
        server.ask_an_expert(question="hi", file_paths=[str(tmp_path / "does-not-exist.txt")])


# --- _resolve_attachment ---


def test_resolve_attachment_resolves_a_relative_path_against_the_base_dir(monkeypatch, tmp_path):
    note = tmp_path / "nuru" / "battery-life-modeling.md"
    note.parent.mkdir()
    note.write_text("x")
    monkeypatch.setattr(server, "BASE_DIR", tmp_path)

    assert server._resolve_attachment("nuru/battery-life-modeling.md") == str(note)


def test_resolve_attachment_leaves_an_absolute_path_alone(monkeypatch, tmp_path):
    note = tmp_path / "elsewhere.md"
    note.write_text("x")
    monkeypatch.setattr(server, "BASE_DIR", tmp_path / "unused")

    assert server._resolve_attachment(str(note)) == str(note)


def test_resolve_attachment_without_a_base_dir_keeps_the_path_as_given(monkeypatch):
    monkeypatch.setattr(server, "BASE_DIR", None)

    assert server._resolve_attachment("nuru/note.md") == "nuru/note.md"


def test_resolve_attachment_names_the_base_dir_when_the_file_is_missing(monkeypatch, tmp_path):
    """The caller cannot see this server's filesystem, so the error has to say where
    it looked - otherwise the only retry available is the identical call again."""
    monkeypatch.setattr(server, "BASE_DIR", tmp_path)

    with pytest.raises(FileNotFoundError, match=str(tmp_path)):
        server._resolve_attachment("nuru/missing.md")


def test_ask_an_expert_attaches_a_vault_relative_path(monkeypatch, tmp_path):
    note = tmp_path / "nuru" / "note.md"
    note.parent.mkdir()
    note.write_text("x")
    monkeypatch.setattr(server, "BASE_DIR", tmp_path)

    seen = {}

    def fake_resolve(provider, db_path, file_path):
        seen["path"] = file_path
        return "file_abc"

    monkeypatch.setattr(server, "resolve_file_id", fake_resolve)
    provider = MagicMock(spec=Provider)
    provider.send_message.return_value = {"ok": True}
    monkeypatch.setattr(server, "get_provider", lambda: provider)

    server.ask_an_expert(question="q", file_paths=["nuru/note.md"])

    assert seen["path"] == str(note)


def test_ask_an_expert_asks_for_more_than_the_contract_default_token_budget(monkeypatch):
    """The expert's reasoning is billed against the same budget as its reply, so the
    generic 1024 default returns a thinking block and no text."""
    monkeypatch.setattr(server, "resolve_file_id", lambda *a: "file_abc")
    provider = MagicMock(spec=Provider)
    provider.send_message.return_value = {"ok": True}
    monkeypatch.setattr(server, "get_provider", lambda: provider)

    server.ask_an_expert(question="q")

    assert provider.send_message.call_args.kwargs["max_tokens"] == server.ASK_AN_EXPERT_MAX_TOKENS
    assert server.ASK_AN_EXPERT_MAX_TOKENS > 1024


def test_write_plan_asks_haiku_at_its_lowest_thinking_for_the_schema(monkeypatch):
    fake_provider = MagicMock(spec=Provider)
    fake_provider.send_message.return_value = {
        "content": [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": '{"steps":[]}'}],
        "stop_reason": "end_turn",
    }
    monkeypatch.setattr(server, "get_provider", lambda: fake_provider)
    schema = {"type": "object", "properties": {"steps": {"type": "array"}}}

    result = server.write_plan(request="Plan this.", instructions="You plan.", schema=schema)

    fake_provider.send_message.assert_called_once_with(
        model="claude-haiku-4-5",
        messages=[{"role": "user", "content": "Plan this."}],
        system="You plan.",
        max_tokens=4096,
        thinking={"type": "enabled", "budget_tokens": 1024},
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    assert result == {"plan": '{"steps":[]}', "stop_reason": "end_turn", "model": "claude-haiku-4-5"}


def test_write_plan_runs_sonnet_at_low_effort_with_the_refusal_fallback(monkeypatch):
    fake_provider = MagicMock(spec=Provider)
    fake_provider.send_message.return_value = {"content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn"}
    monkeypatch.setattr(server, "get_provider", lambda: fake_provider)

    server.write_plan(request="Plan this.", instructions="You plan.", model="claude-sonnet-5-5")

    fake_provider.send_message.assert_called_once_with(
        model="claude-sonnet-5-5",
        messages=[{"role": "user", "content": "Plan this."}],
        system="You plan.",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
        fallbacks="default",
    )


def test_write_plan_refuses_an_unknown_model():
    with pytest.raises(ToolError, match="model must be one of"):
        server.write_plan(request="Plan this.", instructions="You plan.", model="claude-opus-5")


def test_write_plan_says_when_there_is_no_plan(monkeypatch):
    fake_provider = MagicMock(spec=Provider)
    fake_provider.send_message.return_value = {"content": [], "stop_reason": "end_turn"}
    monkeypatch.setattr(server, "get_provider", lambda: fake_provider)

    result = server.write_plan(request="Plan this.", instructions="You plan.")

    assert result["plan"] == "" and "no plan" in result["hint"]
    assert fake_provider.send_message.call_args.kwargs["output_config"] is None
