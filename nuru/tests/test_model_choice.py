"""Picking a conversation's model and thinking level: the models offered (core.main_models), the
Session running on another model with the conversation's history (Session.use_model), and the
service storing the choice per conversation (submit_turn's model / thinking)."""

import pytest

from core import main_models, model_profile, session
from core.service.protocol import ProtocolError
from test_service_host import ME, Harness, reply
from test_session import scripted_chat, start_session

QWEN, GRANITE, HAIKU = "qwen3.5:4b-q8_0", "granite4.2:3b-q8_0", "anthropic:claude-haiku-5-5"


# --------------------------------------------------------------------------- the models offered
def test_thinking_offers_only_the_levels_that_differ():
    assert main_models.thinking(model_profile.for_model(QWEN), True) == (
        [{"id": "off", "label": "Off"}, {"id": "low", "label": "On"}], "off")  # low and high are the same
    assert main_models.thinking(model_profile.for_model(GRANITE), True) == (
        [{"id": "off", "label": "Off"}, {"id": "low", "label": "Low"}, {"id": "high", "label": "High"}], "low")
    assert main_models.thinking(model_profile.for_model(GRANITE), False) == ([], None)


def test_choices_are_supported_pulled_models_then_claude_with_a_key(monkeypatch):
    tags = [{"name": n} for n in (GRANITE, QWEN, "nomic-embed-text-v2-moe:latest", "nuru:latest", "qwen35-planner:4b")]
    monkeypatch.setattr(main_models.requests, "get", lambda url, timeout: type("R", (), {"json": lambda self: {"models": tags}})())
    monkeypatch.setattr(main_models, "model_info", lambda name: {"capabilities": ["completion", "thinking"]})
    monkeypatch.setattr(main_models, "_cache", None)
    monkeypatch.setattr(main_models, "api_key", lambda: "key")

    listed = main_models.choices(QWEN)

    assert listed["default"] == QWEN
    assert [c["id"] for c in listed["choices"]] == [QWEN, GRANITE, HAIKU, "anthropic:claude-sonnet-5-5"]
    assert [c["cloud"] for c in listed["choices"]] == [False, False, True, True]
    assert listed["choices"][2]["label"] == "Claude Haiku 5.5"

    monkeypatch.setattr(main_models, "_cache", None)
    monkeypatch.setattr(main_models, "api_key", lambda: None)
    assert not any(c["cloud"] for c in main_models.choices(QWEN)["choices"]), "no key: no Claude"


# --------------------------------------------------------------------------- the Session on another model
@pytest.fixture
def local(monkeypatch):
    monkeypatch.setattr(session, "BASE_MODEL", QWEN)
    monkeypatch.setattr(session, "CLAUDE", False)
    monkeypatch.setattr(session, "MAIN_MODEL", "")
    unloaded = []
    monkeypatch.setattr(session, "unload_model", unloaded.append)
    return unloaded


def test_switching_runs_the_next_turn_on_the_new_model_with_the_conversations_history(monkeypatch, local):
    seen, models = [], []
    chat = scripted_chat(["Rome, in May.", "Pack light."], seen)

    def recording(model, messages, **kwargs):
        models.append((model, kwargs.get("think")))
        yield from chat(model, messages, **{k: v for k, v in kwargs.items() if k in ("num_ctx", "think", "tools")})

    s = start_session(monkeypatch, chat=recording)
    monkeypatch.setattr(session, "ollama_chat", recording)
    list(s.ask("Where am I going?"))

    s.use_model(GRANITE, "high")
    assert local == [session.MODEL], "the model it replaces is unloaded"
    list(s.ask("What should I pack?"))

    assert models[0][0] == session.MODEL
    assert models[1] == ("nuru-granite4.2-3b-q8_0", "high")
    texts = [m["content"] for m in seen[1] if m["role"] in ("user", "assistant")]
    assert any("Where am I going?" in t for t in texts) and "Rome, in May." in texts, "the history goes to the new model"
    assert s.model_id() == GRANITE
    s.close()


def test_thinking_defaults_to_the_new_models_level_and_switching_back_restores_the_configured_one(monkeypatch, local):
    s = start_session(monkeypatch)
    s.use_model(GRANITE)
    assert s._think() == "low", "Granite's default level"
    s.use_model(GRANITE, "off")
    assert s._think() is False
    s.use_model(None)
    assert s._model() == session.MODEL and s._think() == session.THINK and s.model_id() == QWEN
    assert local[-1] == "nuru-granite4.2-3b-q8_0"
    with pytest.raises(ValueError):
        s.use_model(None, "medium")
    s.close()


def test_switching_to_claude_sends_its_system_prompt_and_unloads_the_local_model(monkeypatch, local):
    sent = []

    def claude_chat(model, messages, **kwargs):
        sent.append((model, kwargs.get("system"), kwargs.get("think")))
        yield "Hello from Claude."
        yield {"prompt_tokens": 1, "completion_tokens": 1}

    s = start_session(monkeypatch)
    monkeypatch.setattr(session.claude_provider, "chat", claude_chat)
    s.use_model(HAIKU, "low")
    events = list(s.ask("hi"))

    assert local == [session.MODEL]
    assert sent and sent[0][0] == "claude-haiku-5-5" and sent[0][2] == "low"
    assert sent[0][1] and "Nuru" in sent[0][1], "Claude gets system.md, rendered"
    assert events[-1].text == "Hello from Claude."
    s.close()


def test_a_title_is_written_by_the_model_the_turn_ran_on_at_its_window(monkeypatch, local):
    asked = []
    monkeypatch.setattr(session.titles, "title", lambda *args, stop: asked.append((*args, stop)) or "Rome trip")
    s = start_session(monkeypatch, model_info={"context_window": 16384})
    list(s.ask("hi"))
    stop = lambda: False  # noqa: E731
    assert s.title("hi", "hello", stop) == "Rome trip"
    assert asked == [("hi", "hello", session.MODEL, 16384, stop)], "the window the turn asked for: Ollama keeps the model as it is"
    s.use_model(GRANITE)
    s.title("hi", "hello", stop)
    assert asked[-1][2] == "nuru-granite4.2-3b-q8_0"
    sent = []
    def claude_chat(model, messages, **kwargs):
        sent.append((model, messages[0]["content"], kwargs))
        yield "Rome by Claude"
    monkeypatch.setattr(session.claude_provider, "chat", claude_chat)
    s.use_model(HAIKU)
    assert s.title("hi", "hello", stop) == "Rome by Claude" and len(asked) == 2, \
        "on Claude, Claude names it: choosing it already sends it the conversation"
    assert sent[0][0] == "claude-haiku-5-5" and "two to six words" in sent[0][1] and sent[0][2] == {"think": False}
    s.close()


# --------------------------------------------------------------------------- the service
MODELS = {"default": QWEN, "choices": [
    {"id": QWEN, "label": QWEN, "cloud": False, "thinking": [{"id": "off", "label": "Off"}, {"id": "low", "label": "On"}],
     "default_thinking": "off"},
    {"id": GRANITE, "label": GRANITE, "cloud": False, "default_thinking": "low",
     "thinking": [{"id": "off", "label": "Off"}, {"id": "low", "label": "Low"}, {"id": "high", "label": "High"}]},
]}


@pytest.fixture
def served(tmp_path):
    h = Harness(tmp_path, [reply("one"), reply("two"), reply("three")], models=lambda: MODELS)
    used = []
    h.session.use_model = lambda model, thinking: used.append((model, thinking))
    yield h, used
    h.close()


def test_a_conversation_keeps_its_model_and_thinking_until_changed(served):
    h, used = served
    conversation = h.conversation()
    h.finished(h.submit(conversation, model=GRANITE, thinking="high")[1]["turn_id"])
    h.finished(h.submit(conversation, request_id="req-turn-00000002")[1]["turn_id"])
    detail = h.service.conversation(ME, conversation, None, 10)
    assert (detail["model"], detail["thinking"]) == (GRANITE, "high")
    h.finished(h.submit(conversation, request_id="req-turn-00000003", model=QWEN)[1]["turn_id"])
    assert used == [(GRANITE, "high"), (GRANITE, "high"), (None, None)], "the default is stored as None; thinking resets"
    assert h.service.status()["models"] == MODELS


def test_a_model_or_level_the_service_doesnt_offer_is_refused(served):
    h, used = served
    conversation = h.conversation()
    with pytest.raises(ProtocolError, match="model"):
        h.submit(conversation, model="llama9:70b")
    with pytest.raises(ProtocolError, match="thinking"):
        h.submit(conversation, request_id="req-turn-00000002", thinking="high")  # Qwen has Off/On
    assert not used


def test_a_service_without_a_model_list_refuses_a_model(tmp_path):
    h = Harness(tmp_path, [reply("one")])
    with pytest.raises(ProtocolError, match="one model"):
        h.submit(h.conversation(), model=GRANITE)
    assert "models" not in h.service.status()
    h.close()
