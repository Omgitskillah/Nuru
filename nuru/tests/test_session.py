from core.modelfile import render_modelfile
import hashlib
import json
import os
import threading
from datetime import datetime, timedelta, timezone

import pytest

from core import session, tool_dispatch
from core.conversation_store import ConversationStore
from core.model_profile import ModelProfile
from core.providers.mcp_tools import ToolResult
from core.session import Failure, RetryWithoutThinking, Session, Text, Thinking, ThinkingChunk, ToolCall


@pytest.fixture(autouse=True)
def _isolate_model_settings(monkeypatch):
    # Building the custom Ollama model is a real `ollama create` subprocess
    # call - never let it run for real in a test.
    monkeypatch.setattr(session, "create_model", lambda model, base_model, modelfile_path: render_modelfile(modelfile_path, base_model))
    # THINK comes from the developer's own .env via load_dotenv, so without this the
    # thinking-mode tests pass or fail depending on what the machine is set up to run.
    monkeypatch.setattr(session, "THINK", True)


def fake_chat(chunks):
    def _chat(model, messages, num_ctx=8192, think=True, tools=None):
        yield from chunks

    return _chat


class FakeMCPTools:
    """Stand-in for MCPTools that skips actually spawning the MCP server."""

    def __init__(self, tools=None, tool_results=None):
        # These broad fake schemas advertise tools used by legacy control-flow tests.
        # Dispatch/schema tests pass explicit realistic definitions.
        names = {"get_temperature", "search", "read_note", "calculate", "write_note", "slow_tool", "first", "second"} | set(tool_results or {})
        self._tools = list(tools) if tools is not None else [
            {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
            for name in sorted(names)]
        self._tool_results = tool_results or {}
        self.calls = []
        self.connected = False
        self.closed = False

    def connect(self):
        self.connected = True

    def list_tools(self):
        return self._tools

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self._tool_results.get(name, "")

    def call_tool_result(self, name, arguments):
        return ToolResult(self.call_tool(name, arguments))

    def call_harness_tool(self, name, arguments):
        if name == "note_info":  # the notes server's lookup: these tests' notes count as new ones
            return ToolResult(f"Error executing tool note_info: FileNotFoundError: No note found named '{arguments['name']}'.", is_error=True)
        # No decision layer here: the shadow's calls fail, which it must shrug off.
        return ToolResult("no decision layer in this test", is_error=True)

    def close(self):
        self.closed = True


def start_session(monkeypatch, chat=None, tools=None, model_info=None):
    """A started Session with everything that would touch Ollama or spawn a server faked out."""
    monkeypatch.setattr(session, "ensure_server_running", lambda: None)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model, *_: model_info)
    monkeypatch.setattr(session, "chat", chat or fake_chat([]))
    fake_tools = tools or FakeMCPTools()
    monkeypatch.setattr(session, "MCPTools", lambda server_params, **kwargs: fake_tools)
    return Session.start()


def logged_events(log_dir):
    (log_file,) = log_dir.glob("*.jsonl")
    return [json.loads(line)["event"] for line in log_file.read_text().splitlines()]


def tool_call(name, arguments):
    return {"function": {"name": name, "arguments": arguments}}


def test_start_builds_the_custom_model_from_the_modelfile_before_loading(monkeypatch):
    calls = []
    monkeypatch.setattr(
        session, "create_model", lambda model, base_model, modelfile_path: (calls.append((model, base_model, modelfile_path)) or render_modelfile(modelfile_path, base_model))
    )

    start_session(monkeypatch)

    assert calls == [(session.MODEL, session.BASE_MODEL, session._modelfile_for(session.BASE_MODEL))]


def test_start_can_resume_a_conversation_and_leave_the_model_for_the_first_turn(monkeypatch):
    loaded = []
    monkeypatch.setattr(session, "ensure_server_running", lambda: loaded.append("server"))
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model, *_: loaded.append(model))
    monkeypatch.setattr(session, "MCPTools", lambda server_params, **kwargs: FakeMCPTools())

    s = Session.start(7, load_model=False)

    assert s.conversation_id == 7 and loaded == []
    s.close()


def test_start_with_a_vault_gives_it_to_the_notes_server_and_the_write_ledger(monkeypatch, tmp_path):
    """nuru-serve names its vault. The notes server doesn't inherit this process's VAULT_PATH, so
    without it the server would take .env.obsidian's vault while the ledger took VAULT_PATH's."""
    served, elsewhere = tmp_path / "served", tmp_path / "elsewhere"
    served.mkdir()
    monkeypatch.setenv("VAULT_PATH", str(elsewhere))
    monkeypatch.setattr(session, "ensure_server_running", lambda: None)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model, *_: None)
    given = []
    monkeypatch.setattr(session, "MCPTools", lambda server_params, **kwargs: given.extend(server_params) or FakeMCPTools())
    default = dict(session.OBSIDIAN_SERVER_PARAMS.env)

    s = Session.start(vault=served)

    obsidian = next(p for p in given if p.args == ["run", "obsidian-mcp"])
    assert obsidian.env["VAULT_PATH"] == str(served.resolve()) and obsidian.env["OBSIDIAN_PROTECT_USER_AUTHORED"] == "1"
    assert "deep-think-mcp" not in [p.args[-1] for p in given]  # write_plan has no caller since plan mode went
    assert s._vault_scope == str(served.resolve())
    assert session.OBSIDIAN_SERVER_PARAMS.env == default  # the shared default is untouched
    s.close()


def test_a_served_vault_never_gets_another_vaults_semantic_index(monkeypatch, tmp_path):
    """The notes server gets only the env Session gives it, and would otherwise fill in .env.obsidian's
    OBSIDIAN_SEMANTIC_DB: the index of .env.obsidian's vault, not of the one served."""
    from core import vault_indexer

    env_file = tmp_path / ".env.obsidian"
    env_file.write_text(f"VAULT_PATH={tmp_path / 'real'}\nOBSIDIAN_SEMANTIC_DB={tmp_path / 'real.sqlite3'}\n")
    monkeypatch.setattr(vault_indexer, "OBSIDIAN_ENV", env_file)
    monkeypatch.delenv("OBSIDIAN_SEMANTIC_DB", raising=False)
    monkeypatch.setattr(session, "ensure_server_running", lambda: None)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model, *_: None)
    given = []
    monkeypatch.setattr(session, "MCPTools", lambda server_params, **kwargs: given.extend(server_params) or FakeMCPTools())

    from core import vault_identity
    (tmp_path / "served").mkdir()
    (tmp_path / "real").mkdir()
    own = lambda: tmp_path / "vaults" / vault_identity.read(tmp_path / "served") / "semantic.sqlite3"  # noqa: E731
    for vault, index in ((tmp_path / "served", own), (tmp_path / "real", lambda: tmp_path / "real.sqlite3")):
        given.clear()
        Session.start(vault=vault).close()
        obsidian = next(p for p in given if p.args == ["run", "obsidian-mcp"])
        assert obsidian.env["OBSIDIAN_SEMANTIC_DB"] == str(index().resolve())
    assert not any((tmp_path / "served").glob("*.sqlite3")), "the served vault's own, outside it"


def test_each_conversation_keeps_its_own_evidence_when_the_session_switches(monkeypatch):
    s = start_session(monkeypatch)
    a, b = s.conversation_id, s.store.create_conversation()
    s.shadow.known_ids, s.shadow.last_search_ids = {"id-a"}, ["id-a"]

    s.use_conversation(b)
    assert (s.conversation_id, s.shadow.known_ids, s.shadow.last_search_ids) == (b, set(), [])
    s.shadow.known_ids, s.shadow.last_search_ids = {"id-b"}, ["id-b"]

    s.use_conversation(a)
    assert (s.shadow.known_ids, s.shadow.last_search_ids) == ({"id-a"}, ["id-a"])
    s.use_conversation(b)
    assert (s.shadow.known_ids, s.shadow.last_search_ids) == ({"id-b"}, ["id-b"])
    s.close()

# --- _modelfile_for ---


def test_modelfile_for_picks_the_file_named_after_the_model_family(monkeypatch, tmp_path):
    (tmp_path / "qwen3.5.Modelfile").write_text("FROM __BASE_MODEL__\n")
    monkeypatch.setattr(session, "MODELFILES_DIR", tmp_path)

    assert session._modelfile_for("qwen3.5:9b") == tmp_path / "qwen3.5.Modelfile"


def test_modelfile_for_strips_the_tag_and_any_registry_namespace(monkeypatch, tmp_path):
    (tmp_path / "qwen3.5.Modelfile").write_text("FROM __BASE_MODEL__\n")
    monkeypatch.setattr(session, "MODELFILES_DIR", tmp_path)

    expected = tmp_path / "qwen3.5.Modelfile"
    assert session._modelfile_for("qwen3.5") == expected
    assert session._modelfile_for("library/qwen3.5:latest") == expected


def test_modelfile_for_distinguishes_families_that_share_a_prefix(monkeypatch, tmp_path):
    (tmp_path / "qwen3.Modelfile").write_text("FROM __BASE_MODEL__\n")
    (tmp_path / "qwen3.5.Modelfile").write_text("FROM __BASE_MODEL__\n")
    monkeypatch.setattr(session, "MODELFILES_DIR", tmp_path)

    assert session._modelfile_for("qwen3:8b") == tmp_path / "qwen3.Modelfile"
    assert session._modelfile_for("qwen3.5:9b") == tmp_path / "qwen3.5.Modelfile"


def test_modelfile_for_raises_naming_the_available_families(monkeypatch, tmp_path):
    (tmp_path / "qwen3.5.Modelfile").write_text("FROM __BASE_MODEL__\n")
    (tmp_path / "llama3.1.Modelfile").write_text("FROM __BASE_MODEL__\n")
    monkeypatch.setattr(session, "MODELFILES_DIR", tmp_path)

    with pytest.raises(FileNotFoundError, match="phi4"):
        session._modelfile_for("phi4:14b")

    with pytest.raises(FileNotFoundError, match="llama3.1, qwen3.5"):
        session._modelfile_for("phi4:14b")


def test_every_shipped_modelfile_has_the_base_model_placeholder():
    """create_model() builds whatever FROM line it finds, so a copy-paste slip that
    drops the placeholder would silently build every model from the same base."""
    shipped = sorted(session.MODELFILES_DIR.glob("*.Modelfile"))

    assert shipped, f"no Modelfiles in {session.MODELFILES_DIR}"
    for path in shipped:
        text = path.read_text()
        assert text.startswith("FROM __BASE_MODEL__"), path.name
        # A second FROM silently wins over the rendered one, so NURU_BASE_MODEL would be
        # ignored and every base model would build from whatever that line names.
        froms = [ln for ln in text.splitlines() if ln.startswith("FROM ")]
        assert froms == ["FROM __BASE_MODEL__"], f"{path.name} has {len(froms)} FROM lines: {froms}"


def test_start_connects_mcp_tools_and_passes_them_to_chat(monkeypatch):
    tools = [{"type": "function", "function": {"name": "get_temperature"}}]
    fake_tools = FakeMCPTools(tools=tools)
    seen = {}

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        seen["tools"] = tools
        return iter([])

    s = start_session(monkeypatch, chat=recording_chat, tools=fake_tools)
    list(s.ask("hi"))

    assert fake_tools.connected is True
    assert seen["tools"] == tools


def test_start_offers_the_model_only_its_allowlist(monkeypatch):
    seen = {}

    def recording_mcp_tools(server_params, **kwargs):
        seen.update(kwargs)
        return FakeMCPTools()

    monkeypatch.setattr(session, "ensure_server_running", lambda: None)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model, *_: None)
    monkeypatch.setattr(session, "MCPTools", recording_mcp_tools)

    Session.start()

    assert "write_note" in seen["model_tools"] and "classify_notes" not in seen["model_tools"]
    for harness_only in ("tool_gate", "assess_effort", "gate_write", "check_claim", "rerank", "health"):
        assert harness_only not in seen["model_tools"]


def test_start_closes_what_it_opened_when_a_later_step_fails(monkeypatch):
    fake_tools = FakeMCPTools()
    monkeypatch.setattr(session, "ensure_server_running", lambda: None)
    monkeypatch.setattr(session, "MCPTools", lambda server_params, **kwargs: fake_tools)

    def failing_load(model, *_):
        raise RuntimeError("model would not load")

    monkeypatch.setattr(session, "ensure_model_loaded", failing_load)

    with pytest.raises(RuntimeError, match="model would not load"):
        Session.start()

    assert fake_tools.closed is True


def test_close_releases_the_tools_and_the_database_without_touching_ollama(monkeypatch):
    fake_tools = FakeMCPTools()
    s = start_session(monkeypatch, tools=fake_tools)

    def unexpected(*a, **k):
        raise AssertionError("close must leave the model and the server alone")

    monkeypatch.setattr(session, "unload_all_models", unexpected)
    monkeypatch.setattr(session, "stop_server", unexpected)

    s.close()

    assert fake_tools.closed is True
    with pytest.raises(Exception, match="closed database"):
        s.store.get_messages(s.conversation_id)


def test_shutdown_unloads_models_stops_the_server_and_logs_why(monkeypatch, tmp_path):
    s = start_session(monkeypatch)
    calls = []
    monkeypatch.setattr(session, "unload_all_models", lambda: calls.append("unload"))
    monkeypatch.setattr(session, "stop_server", lambda: calls.append("stop"))

    s.shutdown("/exit")

    assert calls == ["unload", "stop"]
    assert logged_events(tmp_path)[-1] == "session_end"


def test_load_model_starts_the_server_and_loads_the_model(monkeypatch):
    s = start_session(monkeypatch)
    calls = []
    monkeypatch.setattr(session, "ensure_server_running", lambda: calls.append("server") or True)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model, *_: calls.append(("model", model)) or {})

    s.load_model()

    assert calls == ["server", ("model", session.MODEL)]


@pytest.mark.parametrize("info, expected", [
    ({"context_window": 4096}, 4096),      # the model's maximum, below the cap
    ({"context_window": 262144}, 16384),   # capped at MAX_NUM_CTX
    ({}, 8192),                            # no metadata: DEFAULT_NUM_CTX
])
def test_the_model_is_loaded_at_the_window_its_chats_will_use(monkeypatch, info, expected):
    """Loading at another num_ctx than the chats' makes Ollama load the model a second time."""
    s = start_session(monkeypatch)
    monkeypatch.setattr(session, "MAX_NUM_CTX", 16384)
    monkeypatch.setattr(session, "ensure_server_running", lambda: True)
    windows = []
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model, window: windows.append(window(info)) or info)

    s.load_model()

    assert windows == [expected]
    assert session._context_window(s.model_info) == expected  # what the chats ask for


def test_loaded_model_info_returns_the_info_when_the_model_is_loaded(monkeypatch):
    s = start_session(monkeypatch)
    monkeypatch.setattr(session, "is_model_loaded", lambda model: True)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model, *_: {"family": "qwen3"})

    assert s.loaded_model_info() == {"family": "qwen3"}


def test_loaded_model_info_is_none_and_loads_nothing_when_the_model_is_not_loaded(monkeypatch):
    s = start_session(monkeypatch)
    monkeypatch.setattr(session, "is_model_loaded", lambda model: False)

    def unexpected(model, *_):
        raise AssertionError("asking for the info must not load the model")

    monkeypatch.setattr(session, "ensure_model_loaded", unexpected)

    assert s.loaded_model_info() is None


def test_ask_yields_the_reply_as_text_events_and_saves_it(monkeypatch):
    s = start_session(
        monkeypatch, chat=fake_chat(["Hi", " there", {"prompt_tokens": 1, "completion_tokens": 2}])
    )

    events = list(s.ask("hello"))

    assert events == [Text("Hi"), Text(" there")]
    saved = s.store.get_messages(s.conversation_id)
    assert [(m["role"], m["content"], m["tokens_used"]) for m in saved] == [("user", "hello", 1), ("nuru", "Hi there", 2)]


def test_ask_retries_with_thinking_off_when_the_model_only_thinks(monkeypatch):
    think_per_call = []

    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        think_per_call.append(think)
        if think:  # the whole answer goes into the reasoning and the turn ends with no reply
            yield {"thinking": "  The answer is 4.\n"}
            yield {"prompt_tokens": 1, "completion_tokens": 2}
        else:
            yield "4"
            yield {"prompt_tokens": 1, "completion_tokens": 1}

    s = start_session(monkeypatch, chat=chat)

    events = list(s.ask("what is 2+2?"))

    assert events == [Thinking("The answer is 4."), RetryWithoutThinking(), Text("4")]
    assert think_per_call == [True, False]
    saved = s.store.get_messages(s.conversation_id)  # what the retry wrote is the reply of record
    assert [(m["role"], m["content"]) for m in saved] == [("user", "what is 2+2?"), ("nuru", "4")]


def test_ask_says_no_reply_was_written_when_the_retry_stays_silent_too(monkeypatch):
    # fake_chat ignores `think`, so the retry comes back just as silent as the first call.
    s = start_session(
        monkeypatch,
        chat=fake_chat([{"thinking": "  The answer is 4.\n"}, {"prompt_tokens": 1, "completion_tokens": 2}]),
    )

    events = list(s.ask("what is 2+2?"))

    assert events == [
        Thinking("The answer is 4."),
        RetryWithoutThinking(),  # and only the one: a turn is never retried twice
        Thinking("The answer is 4."),
        Text(session._NO_REPLY_NOTE),
    ]
    saved = s.store.get_messages(s.conversation_id)  # the reasoning is still what the model said
    assert [(m["role"], m["content"]) for m in saved] == [("user", "what is 2+2?"), ("nuru", "The answer is 4.")]


def test_ask_does_not_retry_when_thinking_was_off_to_begin_with(monkeypatch):
    monkeypatch.setattr(session, "THINK", False)
    think_per_call = []

    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        think_per_call.append(think)
        yield {"prompt_tokens": 1, "completion_tokens": 0}

    s = start_session(monkeypatch, chat=chat)

    assert list(s.ask("hello")) == []
    assert think_per_call == [False]  # nothing was routed into reasoning, so there is nothing to retry


def test_ask_shows_the_thinking_alongside_a_reply_the_model_did_write(monkeypatch):
    s = start_session(
        monkeypatch, chat=fake_chat(["4", {"thinking": "2+2"}, {"prompt_tokens": 1, "completion_tokens": 2}])
    )

    assert list(s.ask("what is 2+2?")) == [Text("4"), Thinking("2+2")]


def test_ask_streams_the_reasoning_as_it_comes_then_gives_the_whole(monkeypatch):
    responses = iter([
        [{"thinking_chunk": "I should "}, {"thinking_chunk": "look this up"}, {"thinking": "I should look this up"},
         {"tool_calls": [tool_call("get_temperature", {"city": "Berlin"})]}, {"prompt_tokens": 1, "completion_tokens": 1}],
        ["It's 17C", {"prompt_tokens": 2, "completion_tokens": 2}],
    ])
    s = start_session(monkeypatch, chat=lambda *a, **k: next(responses),
                      tools=FakeMCPTools(tool_results={"get_temperature": "17C"}))

    events = list(s.ask("what's the weather in Berlin?"))

    assert events == [
        ThinkingChunk("I should "), ThinkingChunk("look this up"), Thinking("I should look this up"),
        ToolCall("get_temperature", {"city": "Berlin"}), Text("It's 17C"),
    ]


def test_ask_shows_the_thinking_that_comes_with_a_tool_call(monkeypatch):
    responses = iter(
        [
            [
                {"thinking": "I should look this up"},
                {"tool_calls": [tool_call("get_temperature", {"city": "Berlin"})]},
                {"prompt_tokens": 1, "completion_tokens": 1},
            ],
            ["It's 17C", {"prompt_tokens": 2, "completion_tokens": 2}],
        ]
    )
    s = start_session(
        monkeypatch,
        chat=lambda *a, **k: next(responses),
        tools=FakeMCPTools(tool_results={"get_temperature": "17C"}),
    )

    events = list(s.ask("what's the weather in Berlin?"))

    assert events == [
        Thinking("I should look this up"),
        ToolCall("get_temperature", {"city": "Berlin"}),
        Text("It's 17C"),
    ]


def test_ask_says_nothing_when_the_model_writes_neither_a_reply_nor_thinking(monkeypatch):
    s = start_session(monkeypatch, chat=fake_chat([{"prompt_tokens": 1, "completion_tokens": 0}]))

    assert list(s.ask("hello")) == []


def test_ask_checks_the_server_and_the_model_before_each_send(monkeypatch):
    calls = []
    monkeypatch.setattr(session, "ensure_server_running", lambda: calls.append("server"))
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model, *_: calls.append(("model", model)))
    monkeypatch.setattr(session, "chat", fake_chat([]))
    monkeypatch.setattr(session, "MCPTools", lambda server_params, **kwargs: FakeMCPTools())

    s = Session.start()
    list(s.ask("hi"))

    assert calls == ["server", ("model", session.MODEL), "server", ("model", session.MODEL)]


def test_ask_includes_prior_turns_from_the_same_conversation_as_history(monkeypatch):
    monkeypatch.setattr(session, "_timestamp_line", lambda now: "[NOW]")
    sent = []

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        sent.append(messages)
        return iter([])

    s = start_session(monkeypatch, chat=recording_chat)
    list(s.ask("first"))
    list(s.ask("second"))

    assert sent[0] == [{"role": "user", "content": "[NOW]\n\nfirst"}]
    assert sent[1] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "[NOW]\n\nsecond"},
    ]


def test_timestamp_line_gives_the_weekday_date_time_and_zone():
    now = datetime(2026, 9, 21, 17, 52, tzinfo=timezone(timedelta(hours=2), "CEST"))

    assert session._timestamp_line(now) == "[Current date and time: Monday 2026-09-21 17:52 CEST]"


def test_ask_sends_the_current_date_and_time_ahead_of_the_message_but_saves_it_as_typed(monkeypatch):
    monkeypatch.setattr(session, "_timestamp_line", lambda now: "[NOW]")
    sent = []

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        sent.append([dict(m) for m in messages])
        return iter([])

    s = start_session(monkeypatch, chat=recording_chat)
    list(s.ask("what day is it?"))

    assert sent[0] == [{"role": "user", "content": "[NOW]\n\nwhat day is it?"}]
    saved = s.store.get_messages(s.conversation_id)
    assert [(m["role"], m["content"]) for m in saved] == [("user", "what day is it?"), ("nuru", "")]


def test_ask_keeps_the_date_and_time_ahead_of_the_message_through_a_tool_round(monkeypatch):
    monkeypatch.setattr(session, "_timestamp_line", lambda now: "[NOW]")
    responses = iter([[{"tool_calls": [tool_call("get_temperature", {})]}], []])
    sent = []

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        sent.append([dict(m) for m in messages])
        return iter(next(responses))

    s = start_session(
        monkeypatch, chat=recording_chat, tools=FakeMCPTools(tool_results={"get_temperature": "17C"})
    )
    list(s.ask("weather?"))

    assert sent[0][0] == {"role": "user", "content": "[NOW]\n\nweather?"}
    assert sent[1][0] == {"role": "user", "content": "[NOW]\n\nweather?"}


def test_ask_passes_the_think_setting_to_chat(monkeypatch):
    monkeypatch.setattr(session, "THINK", False)
    seen = {}

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        seen["think"] = think
        return iter([])

    list(start_session(monkeypatch, chat=recording_chat).ask("hi"))

    assert seen["think"] is False


def test_ask_requests_the_capped_context_window_from_chat(monkeypatch):
    seen = {}

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        seen["num_ctx"] = num_ctx
        return iter([])

    list(start_session(monkeypatch, chat=recording_chat, model_info={"context_window": 262144}).ask("hi"))

    assert seen["num_ctx"] == session.MAX_NUM_CTX


def test_ask_runs_a_tool_call_and_sends_the_result_back_to_the_model(monkeypatch):
    fake_tools = FakeMCPTools(tool_results={"get_temperature": "17C"})
    responses = iter(
        [
            [{"tool_calls": [tool_call("get_temperature", {"city": "Berlin"})]}, {"prompt_tokens": 1, "completion_tokens": 1}],
            ["It's 17C", {"prompt_tokens": 2, "completion_tokens": 2}],
        ]
    )
    sent = []

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        sent.append([dict(m) for m in messages])
        yield from next(responses)

    s = start_session(monkeypatch, chat=recording_chat, tools=fake_tools)

    events = list(s.ask("what's the weather in Berlin?"))

    assert events == [ToolCall("get_temperature", {"city": "Berlin"}), Text("It's 17C")]
    assert fake_tools.calls == [("get_temperature", {"city": "Berlin"})]
    assert sent[1][-1] == {"role": "tool", "content": "17C"}


def test_each_shown_tool_call_reports_its_outcome_by_its_own_id(monkeypatch):
    fake_tools = FakeMCPTools(tool_results={"get_temperature": "17C", "read_note": "a note",
                                            "write_note": '{"done": true, "path": "nuru/trip-plan.md"}'})
    responses = iter([[{"tool_calls": [tool_call("get_temperature", {"city": "Berlin"}),
                                       tool_call("write_note", {"note": "Trip plan", "content": "x"})]}], ["It's 17C"]])
    s = start_session(monkeypatch, chat=lambda *a, **k: iter(next(responses)), tools=fake_tools)
    reported = []
    s.on_tool_outcome = lambda call_id, name, arguments, outcome, write: reported.append((call_id, name, outcome.success, write))

    events = list(s.ask("weather, and save the plan"))
    s._call_tool("turn", "read_note", {"name": "x"}, None, None, harness=True)  # as run_python reads its notes
    s._call_tool("turn", "write_note", {"note": "y"}, None, None,  # a refusal never shown (tools closed)
                 refusal=tool_dispatch.failure("policy", "TOOLS_CLOSED", "no"))

    shown_ids = [e.call_id for e in events if isinstance(e, ToolCall)]
    assert reported == [(shown_ids[0], "get_temperature", True, False), (shown_ids[1], "write_note", True, True)]
    assert len(set(shown_ids)) == 2 and all(shown_ids)
    ledger = s.store._conn.execute("SELECT call_id, state, target FROM write_attempts").fetchall()
    assert ledger == [(shown_ids[1], "completed", "nuru/trip-plan.md")]  # the note it changed, by the call's ID
    s.close()


def test_ask_yields_the_tool_call_before_running_it(monkeypatch):
    fake_tools = FakeMCPTools()
    responses = iter([[{"tool_calls": [tool_call("slow_tool", {})]}], []])
    s = start_session(monkeypatch, chat=lambda *a, **k: iter(next(responses)), tools=fake_tools)

    turn = s.ask("go")
    event = next(turn)

    assert event == ToolCall("slow_tool", {})
    assert fake_tools.calls == []  # a UI gets to show the call while the tool is still running
    list(turn)
    assert fake_tools.calls == [("slow_tool", {})]


def test_ask_accepts_tool_arguments_sent_as_a_json_string(monkeypatch):
    fake_tools = FakeMCPTools()
    responses = iter([[{"tool_calls": [tool_call("get_temperature", '{"city": "Berlin"}')]}], []])
    s = start_session(monkeypatch, chat=lambda *a, **k: iter(next(responses)), tools=fake_tools)

    events = list(s.ask("weather?"))

    assert events == [ToolCall("get_temperature", {"city": "Berlin"})]
    assert fake_tools.calls == [("get_temperature", {"city": "Berlin"})]


def test_ask_fails_and_forgets_the_message_when_the_request_fails(monkeypatch):
    def failing_chat(model, messages, num_ctx=8192, think=True, tools=None):
        raise session.requests.exceptions.ReadTimeout("timed out")
        yield  # pragma: no cover - makes this a generator

    s = start_session(monkeypatch, chat=failing_chat)

    first = list(s.ask("hello"))
    second = list(s.ask("still here?"))  # the session keeps working after a failure

    for events in (first, second):
        assert len(events) == 1
        assert isinstance(events[0], Failure)
        assert "request to the model failed: timed out" in events[0].message
        assert "Your message was not saved" in events[0].message
    assert s.store.get_messages(s.conversation_id) == []


def test_ask_rejects_a_message_that_would_exceed_the_token_budget(monkeypatch):
    def unexpected_chat(*a, **k):
        raise AssertionError("chat should not be called for an over-budget message")

    s = start_session(monkeypatch, chat=unexpected_chat, model_info={"context_window": 1000})

    events = list(s.ask("x" * 100_000))

    assert len(events) == 1
    assert isinstance(events[0], Failure)
    assert events[0].message.startswith("message too long")
    assert s.store.get_messages(s.conversation_id) == []


def test_ask_accepts_a_message_within_the_token_budget(monkeypatch):
    s = start_session(
        monkeypatch,
        chat=fake_chat(["ok", {"prompt_tokens": 1, "completion_tokens": 1}]),
        model_info={"context_window": 8192},
    )

    assert list(s.ask("hi")) == [Text("ok")]


def test_ask_stops_when_cancelled_and_keeps_what_was_said_so_far(monkeypatch, tmp_path):
    cancel = threading.Event()

    def chat_that_gets_stopped(model, messages, num_ctx=8192, think=True, tools=None):
        yield "Hel"
        cancel.set()  # the user hits stop while the model is still talking
        yield "lo"
        yield {"prompt_tokens": 1, "completion_tokens": 2}

    s = start_session(monkeypatch, chat=chat_that_gets_stopped)

    events = list(s.ask("hi", cancel))

    assert events == [Text("Hel")]
    saved = s.store.get_messages(s.conversation_id)
    assert [(m["role"], m["content"]) for m in saved] == [("user", "hi"), ("nuru", "Hel")]
    assert logged_events(tmp_path)[-1] == "turn_end"


def test_ask_runs_no_more_tools_once_cancelled(monkeypatch):
    cancel = threading.Event()

    class StoppingTools(FakeMCPTools):
        def call_tool(self, name, arguments):
            cancel.set()  # stop pressed while the first tool is running
            return super().call_tool(name, arguments)

    fake_tools = StoppingTools()
    calls = [tool_call("first", {}), tool_call("second", {})]
    chats = []

    def recording_chat(model, messages, num_ctx=8192, think=True, tools=None):
        chats.append(messages)
        yield {"tool_calls": calls}

    s = start_session(monkeypatch, chat=recording_chat, tools=fake_tools)

    events = list(s.ask("go", cancel))

    assert [e.name for e in events if isinstance(e, ToolCall)] == ["first"]
    assert fake_tools.calls == [("first", {})]
    assert len(chats) == 1  # and the model wasn't asked to continue


def test_estimate_tokens_uses_chars_per_token_heuristic():
    assert session._estimate_tokens("a" * 400) == 100


def test_token_budget_reserves_the_response_fraction():
    assert session._token_budget(1000) == 800


def test_context_window_is_capped_at_max_num_ctx():
    assert session._context_window({"context_window": 262144}) == session.MAX_NUM_CTX


def test_context_window_passes_a_smaller_model_window_through_unchanged():
    assert session._context_window({"context_window": 4096}) == 4096


def test_context_window_falls_back_to_the_default_when_model_info_is_missing():
    assert session._context_window(None) == session.DEFAULT_NUM_CTX
    assert session._context_window({}) == session.DEFAULT_NUM_CTX


def test_the_store_can_be_used_from_another_thread(tmp_path):
    # The UI starts the session on one thread and runs each turn on another.
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    errors = []

    def use_store():
        try:
            store.add_message(conversation_id, "user", "hi")
        except Exception as e:  # pragma: no cover - only reached if the store refuses
            errors.append(e)

    worker = threading.Thread(target=use_store)
    worker.start()
    worker.join()

    assert errors == []


# --- model profiles ---


def test_ask_sends_the_profiles_sampling_for_thinking_on_or_off(monkeypatch):
    profile = ModelProfile({"off": False, "low": True, "high": True}, "off",
                           {"thinking": {"temperature": 0.6}, "no_thinking": {"temperature": 0.7}})
    monkeypatch.setattr(session, "PROFILE", profile)
    monkeypatch.setattr(session, "THINK", False)
    seen = []

    def chat(model, messages, num_ctx=8192, think=True, tools=None, options=None):
        seen.append((think, options))
        yield "ok"
        yield {"prompt_tokens": 1, "completion_tokens": 1}

    s = start_session(monkeypatch, chat=chat)
    list(s.ask("hi"))

    assert seen == [(False, {"temperature": 0.7})]


def test_a_reasoning_level_still_triggers_the_silent_turn_retry(monkeypatch):
    """The retry checks `think` for truthiness, so a level must not bypass it."""
    monkeypatch.setattr(session, "THINK", "medium")
    seen = []

    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        seen.append(think)
        if think:  # silent turn: everything went into the reasoning
            yield {"thinking": "the answer is 4"}
            yield {"prompt_tokens": 1, "completion_tokens": 1}
        else:
            yield "4"
            yield {"prompt_tokens": 1, "completion_tokens": 1}

    s = start_session(monkeypatch, chat=chat)
    events = list(s.ask("what is 2+2?"))

    assert seen == ["medium", False]
    assert events == [Thinking("the answer is 4"), RetryWithoutThinking(), Text("4")]


class FakeIndexer:
    def __init__(self):
        self.pending = 0
        self.changes = 0
        self.last_change = ""
        self.on_change = None
        self.calls = []

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")


def test_start_runs_the_background_indexer_and_close_stops_it(monkeypatch):
    indexer = FakeIndexer()
    monkeypatch.setattr(session, "VaultIndexer", type("V", (), {"from_env": staticmethod(lambda: indexer)}))
    s = start_session(monkeypatch)

    assert indexer.calls == ["start"]
    assert s.indexing == 0
    assert callable(indexer.on_change)  # wired to the session log
    indexer.pending = 7
    assert s.indexing == 7

    s.close()

    assert indexer.calls == ["start", "stop"]


def test_a_session_without_a_vault_has_no_indexer(monkeypatch):
    s = start_session(monkeypatch)

    assert s.indexer is None
    assert s.indexing == 0
    s.close()


def context_overflow_error():
    """What chat() raises when Ollama rejects a request longer than num_ctx."""
    response = session.requests.Response()
    response.status_code = 400
    response._content = (
        b'{"error":"request (23401 tokens) exceeds the available context size (16384 tokens), try increasing it"}'
    )
    return session.requests.exceptions.HTTPError("400 Client Error: Bad Request", response=response)


def test_a_tool_result_too_long_for_the_context_is_swapped_for_a_note_and_the_turn_goes_on(monkeypatch):
    fake_tools = FakeMCPTools(tool_results={"read_note": "x" * 4000})
    seen = []
    calls = iter(["tool", "overflow", "answer"])

    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        seen.append([dict(m) for m in messages])
        step = next(calls)
        if step == "tool":
            yield {"tool_calls": [tool_call("read_note", {"message": "audit"})]}
        elif step == "overflow":
            raise context_overflow_error()
        else:
            yield "Here is what I found."
            yield {"prompt_tokens": 10, "completion_tokens": 5}

    s = start_session(monkeypatch, chat=chat, tools=fake_tools)

    events = list(s.ask("audit my notes"))

    assert events == [ToolCall("read_note", {"message": "audit"}), Text("Here is what I found.")]
    assert seen[1][-1]["content"] == "x" * 4000  # the request Ollama refused
    note = seen[2][-1]["content"]
    assert note.startswith("[The read_note result was about")
    assert "too long for the context window" in note
    assert s.store.get_messages(s.conversation_id)[-1]["content"] == "Here is what I found."


def test_a_second_overflow_in_the_same_turn_ends_it_as_before(monkeypatch):
    fake_tools = FakeMCPTools(tool_results={"read_note": "x" * 4000})
    calls = iter(["tool", "overflow", "overflow"])

    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        if next(calls) == "tool":
            yield {"tool_calls": [tool_call("read_note", {"message": "audit"})]}
        else:
            raise context_overflow_error()

    s = start_session(monkeypatch, chat=chat, tools=fake_tools)

    events = list(s.ask("audit my notes"))

    assert isinstance(events[-1], Failure)
    assert "request to the model failed" in events[-1].message


def test_an_overflow_with_no_tool_result_to_cut_ends_the_turn_as_before(monkeypatch):
    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        raise context_overflow_error()
        yield  # pragma: no cover - makes this a generator

    s = start_session(monkeypatch, chat=chat)

    events = list(s.ask("hello"))

    assert len(events) == 1 and isinstance(events[0], Failure)
    assert s.store.get_messages(s.conversation_id) == []


def scripted_chat(steps, seen=None):
    """A chat() that plays one step per call: a list of tool calls to make, or reply text."""
    steps = iter(steps)

    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        if seen is not None:
            seen.append([dict(m) for m in messages])
        step = next(steps)
        if isinstance(step, list):
            yield {"tool_calls": step}
        else:
            yield step
            yield {"prompt_tokens": 1, "completion_tokens": 1}

    return chat


def test_a_tool_that_returns_an_error_is_logged_as_an_error(monkeypatch, tmp_path):
    fake_tools = FakeMCPTools(tools=[{"type": "function", "function": {"name": "read_note", "description": "x", "parameters": {}}}])
    fake_tools.call_tool_result = lambda name, arguments: ToolResult("FileNotFoundError: no note 'x'", is_error=True)
    chat = scripted_chat([[tool_call("read_note", {"name": "x"})], "No such note."])
    s = start_session(monkeypatch, chat=chat, tools=fake_tools)

    list(s.ask("read x"))

    (log_file,) = tmp_path.glob("*.jsonl")
    (call,) = [e for e in map(json.loads, log_file.read_text().splitlines()) if e["event"] == "tool_call"]
    assert call["status"] == "error" and "no note 'x'" in call["error"]


def test_start_connects_the_obsidian_math_and_laya_servers(monkeypatch):
    seen = {}

    def recording_mcp_tools(server_params, **kwargs):
        seen["params"] = server_params
        return FakeMCPTools()

    monkeypatch.setattr(session, "ensure_server_running", lambda: None)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model, *_: None)
    monkeypatch.setattr(session, "MCPTools", recording_mcp_tools)
    monkeypatch.setattr(session, "python_server_params", lambda: None)  # no Docker: no run_python

    Session.start()

    assert [p.args[-1] for p in seen["params"]] == ["obsidian-mcp", "math-mcp", "laya-mcp"]  # this checkout: cargo


def test_a_release_install_runs_its_prebuilt_calculator_and_never_asks_for_rust(monkeypatch, tmp_path):
    """A release install has servers/math-4-llms/bin/math-mcp and no .git: Nuru runs that binary. Without
    the binary it doesn't start, and says how to repair it, rather than fall back to cargo."""
    seen = {}
    monkeypatch.setattr(session, "ensure_server_running", lambda: None)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model, *_: None)
    monkeypatch.setattr(session, "MCPTools", lambda params, **kwargs: seen.setdefault("params", params) and FakeMCPTools())
    monkeypatch.setattr(session, "python_server_params", lambda: None)
    monkeypatch.setattr(session, "ENV_DIR", tmp_path)  # a tree with no .git, as a release unpacks
    binary = tmp_path / "bin" / ("math-mcp.exe" if os.name == "nt" else "math-mcp")
    monkeypatch.setattr(session, "MATH_BINARY", binary)

    with pytest.raises(RuntimeError, match="calculator is missing.*install Nuru again"):
        Session.start()
    assert "params" not in seen

    binary.parent.mkdir()
    binary.write_bytes(b"not the released calculator")
    released = {"math-mcp": {"version": "v1.0.0", "systems": {session.this_system(): {"sha256": "0" * 64}}}}
    monkeypatch.setattr(session, "DEPENDENCIES", tmp_path / "dependencies.json")
    session.DEPENDENCIES.write_text(json.dumps(released), encoding="utf-8")
    with pytest.raises(RuntimeError, match="isn't math-mcp v1.0.0 for this computer: install Nuru again"):
        Session.start()
    assert "params" not in seen

    released["math-mcp"]["systems"][session.this_system()]["sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    session.DEPENDENCIES.write_text(json.dumps(released), encoding="utf-8")
    Session.start()
    assert seen["params"][1].command == str(binary) and seen["params"][1].args == []
    assert all(p.command != "cargo" for p in seen["params"])


def test_on_a_computer_with_no_released_calculator_nuru_says_so_and_windows_on_arm_uses_x64(monkeypatch, tmp_path):
    binary = tmp_path / "math-mcp"
    binary.write_bytes(b"x64 calculator")
    monkeypatch.setattr(session, "MATH_BINARY", binary)
    monkeypatch.setattr(session, "DEPENDENCIES", tmp_path / "dependencies.json")
    session.DEPENDENCIES.write_text(json.dumps({"math-mcp": {"version": "v1.0.0", "systems": {
        "win32-x64": {"sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}}}}), encoding="utf-8")
    monkeypatch.setattr(session, "this_system", lambda: "win32-arm64")
    assert session.math_server_params().command == str(binary)
    monkeypatch.setattr(session, "this_system", lambda: "linux-x64")
    with pytest.raises(RuntimeError, match=r"no calculator for this computer \(linux-x64\)") as refused:
        session.math_server_params()
    assert "install Nuru again" not in str(refused.value)


def test_the_pinned_calculator_release_covers_every_system_nuru_ships_on():
    """release/dependencies.json, which the session and the plugin's setup read: each system's asset and
    its sha256 from the release's SHA256SUMS."""
    release = json.loads(session.DEPENDENCIES.read_text(encoding="utf-8"))["math-mcp"]
    assert release["url"] == f"https://github.com/Omgitskillah/math-4-llms/releases/download/{release['version']}/"
    assert set(release["systems"]) == {"darwin-arm64", "darwin-x64", "win32-x64"}
    for system in release["systems"].values():
        assert set(system) == {"asset", "sha256"} and len(system["sha256"]) == 64 and int(system["sha256"], 16) >= 0
    assert release["systems"]["win32-x64"]["asset"].endswith(".exe")


def test_start_adds_the_python_server_when_it_can_run(monkeypatch):
    seen = {}

    def recording_mcp_tools(server_params, **kwargs):
        seen["params"] = server_params
        return FakeMCPTools()

    monkeypatch.setattr(session, "ensure_server_running", lambda: None)
    monkeypatch.setattr(session, "ensure_model_loaded", lambda model, *_: None)
    monkeypatch.setattr(session, "MCPTools", recording_mcp_tools)
    monkeypatch.setattr(session, "python_server_params",
                        lambda: session.StdioServerParameters(command="cargo", args=["run", "--bin", "python-mcp"]))

    Session.start()

    assert [p.args[-1] for p in seen["params"]][-1] == "python-mcp"


def test_index_change_reports_the_indexers_passes(monkeypatch):
    indexer = FakeIndexer()
    monkeypatch.setattr(session, "VaultIndexer", type("V", (), {"from_env": staticmethod(lambda: indexer)}))
    s = start_session(monkeypatch)
    assert s.index_change == (0, "")

    indexer.changes, indexer.last_change = 2, "indexed 1 note"

    assert s.index_change == (2, "indexed 1 note")
    s.close()


def test_an_index_pass_is_written_to_the_session_log(monkeypatch, tmp_path):
    indexer = FakeIndexer()
    monkeypatch.setattr(session, "VaultIndexer", type("V", (), {"from_env": staticmethod(lambda: indexer)}))
    s = start_session(monkeypatch)
    from obsidian_mcp.semantic import RefreshResult

    indexer.on_change(RefreshResult(embedded=["user/a.md"], moved=[("x.md", "y/x.md")], removed=["z.md"]))

    (log_file,) = tmp_path.glob("*.jsonl")
    events = [e for e in map(json.loads, log_file.read_text().splitlines()) if e["event"] == "index_pass"]
    assert events == [
        {**events[0], "embedded": ["user/a.md"], "moved": [{"from": "x.md", "to": "y/x.md"}], "removed": ["z.md"]}
    ]
    s.close()


def test_admission_counts_system_and_advertised_schemas(monkeypatch):
    tools = FakeMCPTools(tools=[{"function": {"name": "search", "description": "x" * 40000}}])
    s = start_session(monkeypatch, tools=tools)
    events = list(s.ask("hi"))
    assert isinstance(events[0], Failure)
    assert s.store.get_messages(s.conversation_id) == []


def test_admission_uses_reloaded_effective_window(monkeypatch):
    s = start_session(monkeypatch, model_info={"context_window": 32768})
    monkeypatch.setattr(session, "ensure_model_loaded", lambda *_: {"context_window": 1000})
    assert isinstance(list(s.ask("hi"))[0], Failure)
    assert s.context_window == 1000


def test_oversized_continuation_omits_evidence_and_completes(monkeypatch):
    tools = FakeMCPTools(tool_results={"read_note": "x" * 100000})
    calls = []
    def chat(model, messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            yield {"tool_calls": [tool_call("read_note", {"name": "big"})]}
        else:
            yield "The large note is unresolved."
    s = start_session(monkeypatch, tools=tools, chat=chat)
    assert list(s.ask("read big"))[-1] == Text("The large note is unresolved.")
    assert len(calls) == 2
    assert calls[1][-1]["content"].startswith("[left out: ~25000 tokens")
    entries = [json.loads(line) for log in session.LOG_DIR.glob("*.jsonl") for line in log.read_text().splitlines()]
    reduction = next(e for e in entries if e["event"] == "context_reduction")
    assert reduction["omitted_result_tokens"] == [25000] and reduction["turn_id"] == "t-1"
    assert calls[1][-2]["tool_calls"][0]["function"]["name"] == "read_note"
    assert s.store.get_messages(s.conversation_id)[-1]["content"] == "The large note is unresolved."


def test_continuation_evicts_complete_old_exchange_and_keeps_tool_pair(monkeypatch):
    tools = FakeMCPTools(tool_results={"read_note": "x" * 12000})
    calls = []
    def chat(model, messages, **kwargs):
        calls.append([dict(m) for m in messages])
        if len(calls) == 1:
            yield {"tool_calls": [tool_call("read_note", {"name": "big"})]}
        else:
            yield "done"
    s = start_session(monkeypatch, tools=tools, chat=chat)
    s.store.add_message(s.conversation_id, "user", "old" * 1400)
    s.store.add_message(s.conversation_id, "nuru", "answer" * 700)
    assert list(s.ask("read big"))[-1] == Text("done")
    assert len(calls[0]) == 3
    assert [m["role"] for m in calls[1]] == ["user", "assistant", "tool"]
    assert calls[1][1]["tool_calls"][0]["function"]["name"] == "read_note"
    assert calls[1][2]["content"] == "x" * 12000

def test_system_budget_uses_exact_build_rendering_without_reading_again(monkeypatch):
    monkeypatch.setattr(session, "create_model", lambda *args: 'FROM test\nSYSTEM """startup prompt"""\n')
    s = start_session(monkeypatch)
    assert s._system_text() == "startup prompt"
    # The template may change or disappear after startup; accounting stays fixed.
    monkeypatch.setattr(session, "_modelfile_for", lambda _: (_ for _ in ()).throw(AssertionError("re-read")))
    assert s._system_text() == "startup prompt"
    assert list(s.ask("hi")) == []


def test_budget_omits_largest_tools_only_until_fit_and_preserves_original(monkeypatch):
    from core.context_budget import available_tokens
    sent = []
    s = start_session(monkeypatch, chat=lambda model, messages, **kw: (sent.append(messages) or iter(["ok"])))
    messages = [{"role": "system", "content": "explicit"}, {"role": "user", "content": "question"},
                {"role": "assistant", "tool_calls": [tool_call("read_note", {"name": "large"}),
                                                       tool_call("read_note", {"name": "small"})]},
                {"role": "tool", "tool_call_id": "large", "content": "x" * 6000},
                {"role": "tool", "tool_call_id": "small", "content": "important remaining fact"}]
    assert list(s._chat("test", messages, num_ctx=1000)) == ["ok"]
    assert available_tokens(1000, sent[0]) >= 0
    assert sent[0][3]["content"].startswith("[left out:")
    assert sent[0][3]["tool_call_id"] == "large"
    assert sent[0][4] == messages[4]
    assert messages[3]["content"] == "x" * 6000
    assert sent[0][2] == messages[2]


def test_irreducible_request_raises_after_all_tool_evidence_removed(monkeypatch):
    from core.context_budget import ContextBudgetExceeded
    s = start_session(monkeypatch, chat=lambda *a, **kw: pytest.fail("must not send"))
    messages = [{"role": "system", "content": "explicit"}, {"role": "user", "content": "x" * 6000},
                {"role": "tool", "content": "x" * 12000}]
    with pytest.raises(ContextBudgetExceeded):
        list(s._chat("test", messages, num_ctx=1000))


def test_multiple_tool_results_are_omitted_if_one_is_not_enough(monkeypatch):
    sent = []
    s = start_session(monkeypatch, chat=lambda model, messages, **kw: (sent.append(messages) or iter(["ok"])))
    messages = [{"role": "system", "content": "explicit"}, {"role": "user", "content": "question"},
                {"role": "assistant", "tool_calls": [tool_call("read_note", {"name": n}) for n in ("a", "b")]},
                {"role": "tool", "content": "x" * 7000}, {"role": "tool", "content": "y" * 6000}]
    list(s._chat("test", messages, num_ctx=1000))
    assert all(m["content"].startswith("[left out:") for m in sent[0] if m["role"] == "tool")


# ------------------------------------------------------------------ questions to the user
def asking(answers):
    """An answer_question hook that records each question and gives the next scripted answer."""
    asked, answers = [], iter(answers)

    def answer(question):
        asked.append(question)
        return next(answers)

    return answer, asked


def write_then_reply(seen=None):
    return scripted_chat([[tool_call("write_note", {"note": "summary.md", "content": "- one"})], "Here it is."], seen)


def test_a_write_the_message_ruled_out_is_not_run_with_nobody_to_ask(monkeypatch):
    seen = []
    fake_tools = FakeMCPTools()
    s = start_session(monkeypatch, chat=write_then_reply(seen), tools=fake_tools)

    events = list(s.ask("Write a three-bullet summary here. Do not save it."))

    assert fake_tools.calls == []
    tool_message = seen[1][-1]
    assert tool_message["role"] == "tool"
    assert 'write_note was not run: this message says \\"Do not save it\\". Give the result in the reply instead.' in tool_message["content"]
    assert events[-1] == Text("Here it is.")


@pytest.mark.parametrize("answer, runs", [(["Yes"], True), (["No"], False), (None, False)])  # None: no answer in time
def test_a_write_the_message_ruled_out_runs_only_if_the_user_says_yes_when_asked(monkeypatch, answer, runs):
    seen = []
    fake_tools = FakeMCPTools()
    s = start_session(monkeypatch, chat=write_then_reply(seen), tools=fake_tools)
    s.answer_question, asked = asking([answer])

    list(s.ask("Summarise it; do not save yet."))

    assert [q.text for q in asked] == [
        'Your message said "do not save yet", but Nuru wants to run write_note on summary.md. Go ahead anyway?']
    assert asked[0].recommended.label == "No"
    assert (fake_tools.calls == [("write_note", {"note": "summary.md", "content": "- one"})]) is runs
    if answer == ["No"]:
        assert "and the user didn't allow it when asked" in seen[1][-1]["content"]


def test_a_write_the_message_asked_for_runs_without_a_question(monkeypatch):
    fake_tools = FakeMCPTools()
    s = start_session(monkeypatch, chat=write_then_reply(), tools=fake_tools)
    s.answer_question, asked = asking([])

    list(s.ask("Save a three-bullet summary to my notes."))

    assert asked == []
    assert fake_tools.calls == [("write_note", {"note": "summary.md", "content": "- one"})]


ASK = {"question": "Which reporting rate should I use?", "kind": "single",
       "options": [{"label": "4 reports/h", "description": "the revised note", "recommended": True}, "6 reports/h"]}


def offered_tools(steps, offered):
    """scripted_chat, also recording the tool names offered to each call. A step may be (text, calls)."""
    steps = iter(steps)

    def chat(model, messages, num_ctx=8192, think=True, tools=None):
        offered.append([t["function"]["name"] for t in tools or []])
        step = next(steps)
        if isinstance(step, tuple):
            yield step[0]
            yield {"tool_calls": step[1]}
        elif isinstance(step, list):
            yield {"tool_calls": step}
        else:
            yield step
            yield {"prompt_tokens": 1, "completion_tokens": 1}

    return chat


def test_ask_user_is_not_offered_unless_switched_on(monkeypatch):
    offered = []
    s = start_session(monkeypatch, chat=offered_tools([[tool_call("ask_user", ASK)], "Using 4 reports/h."], offered))

    events = list(s.ask("battery life?"))

    assert "ask_user" not in offered[0]
    assert not any(isinstance(e, session.Ask) for e in events)  # refused as a tool it wasn't given


def test_ask_user_ends_the_turn_on_the_question_and_keeps_it_as_the_reply(monkeypatch):
    monkeypatch.setattr(session, "ASK_USER", True)
    offered = []
    fake_tools = FakeMCPTools()
    chat = offered_tools([("The two notes disagree.", [tool_call("ask_user", ASK), tool_call("calculate", {})])], offered)
    s = start_session(monkeypatch, chat=chat, tools=fake_tools)

    events = list(s.ask("battery life?"))

    assert offered[0][-1] == "ask_user"
    assert events[0] == Text("The two notes disagree.")
    assert isinstance(events[-1], session.Ask)
    assert events[-1].question.options[0].label == "4 reports/h"
    assert fake_tools.calls == []  # the call after the question waits for the answer
    kept = s.store.get_recent_messages(s.conversation_id, 10_000)[-1]
    assert kept["role"] == "nuru"
    assert kept["content"] == ("The two notes disagree.\n\nWhich reporting rate should I use?\n\n"
                               "- 4 reports/h (recommended): the revised note\n- 6 reports/h")


def test_the_answer_is_the_next_message_and_the_model_sees_the_question_before_it(monkeypatch):
    monkeypatch.setattr(session, "ASK_USER", True)
    seen = []
    chat = scripted_chat([[tool_call("ask_user", ASK)], "At 4 reports/h: 3.47 years."], seen)
    s = start_session(monkeypatch, chat=chat)

    list(s.ask("battery life?"))
    list(s.ask("4 reports/h"))

    roles = [(m["role"], m["content"].splitlines()[-1]) for m in seen[1]]
    assert roles[-2:] == [("assistant", "- 6 reports/h"), ("user", "4 reports/h")]


def test_a_question_that_makes_no_sense_goes_back_to_the_model_to_fix(monkeypatch):
    monkeypatch.setattr(session, "ASK_USER", True)
    seen = []
    chat = scripted_chat([[tool_call("ask_user", {"question": "Which?", "kind": "single", "options": ["A"]})],
                          [tool_call("ask_user", ASK)]], seen)
    s = start_session(monkeypatch, chat=chat)

    events = list(s.ask("battery life?"))

    assert "single needs 2-6 options" in seen[1][-1]["content"]
    assert isinstance(events[-1], session.Ask)


def logged_questions(log_dir):
    (log_file,) = log_dir.glob("*.jsonl")
    return [entry for entry in map(json.loads, log_file.read_text().splitlines()) if entry["event"] == "question"]


@pytest.mark.parametrize("hook, answer, decision, reason", [
    (True, ["Yes"], "Yes", "answered"), (True, None, "No", "no answer"), (False, None, "No", "nobody there")])
def test_every_question_code_raises_is_logged_with_what_the_turn_went_on_with(monkeypatch, tmp_path, hook, answer, decision, reason):
    monkeypatch.setattr(session, "LOG_DIR", tmp_path)
    s = start_session(monkeypatch, chat=write_then_reply())
    if hook:
        s.answer_question, _ = asking([answer])

    list(s.ask("Summarise it; do not save yet."))

    (entry,) = logged_questions(tmp_path)
    assert entry["question"].startswith('Your message said "do not save yet"')
    assert (entry["answer"], entry["decision"], entry["reason"]) == (answer, decision, reason)
    assert (entry["seconds"] is None) is (not hook)


class SharedVault(FakeMCPTools):
    """Notes named by title resolve as the server would: "Team Handover" is in shared/. A name it
    doesn't know gets the server's not-found error; `lookup` replaces note_info's answer outright."""

    def __init__(self, *args, lookup=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.lookup = lookup

    def call_harness_tool(self, name, arguments):
        if name == "note_info":
            if self.lookup is not None:
                return self.lookup(arguments)
            paths = {"Team Handover": "shared/Team Handover.md", "Priya Raman": "user/Priya Raman.md"}
            if arguments["name"] in paths:
                return ToolResult(json.dumps({"note": paths[arguments["name"]]}))
            return ToolResult(f"Error executing tool note_info: FileNotFoundError: No note found named "
                              f"'{arguments['name']}'. Use browse or search to find the right note.", is_error=True)
        return super().call_harness_tool(name, arguments)


@pytest.mark.parametrize("call, path", [
    (tool_call("write_note", {"note": "shared/Team Handover.md", "content": "x"}), "shared/Team Handover.md"),
    (tool_call("write_note", {"note": "Team Handover", "content": "x"}), "shared/Team Handover.md"),
    (tool_call("rename_or_move_file", {"source_path": "nuru/a.md", "destination_path": "Shared/a.md"}), "Shared/a.md"),
    # Dot components are resolved as the server resolves them (PR #10 review).
    (tool_call("rename_or_move_file", {"source_path": "nuru/private.md", "destination_path": "nuru/../shared/leaked.md"}),
     "shared/leaked.md"),
    (tool_call("rename_or_move_file", {"source_path": "nuru/private.md", "destination_path": "./shared/leaked.md"}),
     "shared/leaked.md"),
    (tool_call("write_note", {"note": "nuru/../shared/Team Handover.md", "content": "x"}), "shared/Team Handover.md"),
])
def test_a_write_into_shared_is_asked_about_and_runs_only_on_a_yes(monkeypatch, call, path):
    for answer, runs in ((["Yes"], True), (None, False)):
        seen, tools = [], SharedVault(tool_results={"rename_or_move_file": ""})
        s = start_session(monkeypatch, chat=scripted_chat([[call], "Done."], seen), tools=tools)
        s.answer_question, asked = asking([answer])

        list(s.ask("Put the handover where the team can see it."))

        assert [q.text for q in asked] == [
            f"Nuru wants to run {call['function']['name']} on {path}, where others can read it. Allow it?"]
        assert asked[0].timeout == 10
        assert bool(tools.calls) is runs
        if not runs:
            assert f"{path} is in shared/, which others read, and the user didn't allow it" in seen[1][-1]["content"]


@pytest.mark.parametrize("arguments", [{"note": "Priya Raman", "content": "x"}, {"note": "Brand new", "content": "x"},
                                       {"type": "daily", "content": "x"}])
def test_writes_outside_shared_are_not_asked_about(monkeypatch, arguments):
    tools = SharedVault()
    s = start_session(monkeypatch, chat=scripted_chat([[tool_call("write_note", arguments)], "Done."]), tools=tools)
    s.answer_question, asked = asking([])

    list(s.ask("Note it down."))

    assert asked == [] and tools.calls == [("write_note", arguments)]


def test_with_nobody_there_a_write_into_shared_does_not_run(monkeypatch):
    seen, tools = [], SharedVault()
    chat = scripted_chat([[tool_call("write_note", {"note": "shared/Team Handover.md", "content": "x"})], "Done."], seen)
    s = start_session(monkeypatch, chat=chat, tools=tools)

    list(s.ask("Add her lunch discussion to the handover."))

    assert tools.calls == []
    assert "nobody was there to allow it" in seen[1][-1]["content"]


def lookup_raises(arguments):
    raise ConnectionError("obsidian server went away")


@pytest.mark.parametrize("lookup", [
    lookup_raises,
    lambda arguments: ToolResult("Error executing tool note_info: ValueError: bad frontmatter", is_error=True),
    lambda arguments: ToolResult("not json"),
    lambda arguments: ToolResult(json.dumps({"hint": "no note key"})),
], ids=["exception", "other error", "unparseable", "no path"])
def test_a_write_whose_note_cant_be_looked_up_is_treated_as_possibly_shared(monkeypatch, lookup):
    # PR #10 review: a failed lookup can't establish that the note is private; the server may still
    # resolve "Handover" to shared/Handover.md.
    for answer, runs in ((["Yes"], True), (["No"], False), (None, False)):
        seen, tools = [], SharedVault(lookup=lookup)
        chat = scripted_chat([[tool_call("write_note", {"note": "Handover", "content": "Private lunch detail"})], "Done."], seen)
        s = start_session(monkeypatch, chat=chat, tools=tools)
        s.answer_question, asked = asking([answer])

        list(s.ask("Add the lunch detail to the handover."))

        assert len(asked) == 1
        assert asked[0].text.startswith("Nuru couldn't check where write_note on Handover would land")
        assert asked[0].timeout == 10
        assert bool(tools.calls) is runs
        if not runs:
            assert "couldn't confirm that Handover is outside shared/" in seen[1][-1]["content"]


def test_with_nobody_there_a_write_whose_note_cant_be_looked_up_does_not_run(monkeypatch):
    seen, tools = [], SharedVault(lookup=lookup_raises)
    chat = scripted_chat([[tool_call("write_note", {"note": "Handover", "content": "x"})], "Done."], seen)
    s = start_session(monkeypatch, chat=chat, tools=tools)

    list(s.ask("Add it to the handover."))

    assert tools.calls == []
    assert "nobody was there to allow it" in seen[1][-1]["content"]
def test_link_auditor_starts_logs_summary_and_stops_with_session(monkeypatch):
    auditor = FakeIndexer()
    monkeypatch.setattr(session, 'VaultLinkAuditor', type('V', (), {'from_env': staticmethod(lambda: auditor)}))
    s = start_session(monkeypatch)
    try:
        assert auditor.calls == ['start']
        assert s.link_auditor is auditor
        auditor.on_change({'snapshot': 'test', 'complete': True, 'counts': {'broken': 1},
                           'diagnostic_count': 0, 'stats': {'model_calls': 0}})
        assert 'link_audit' in logged_events(session.LOG_DIR)
    finally:
        s.close()
    assert auditor.calls == ['start', 'stop']
