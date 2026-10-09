import builtins

import pytest

from core import cli, questions
from core.conversation_store import ConversationStore
from core.session import Ask, Failure, RetryWithoutThinking, Text, Thinking, ThinkingChunk, ToolCall


class FakeSession:
    """Stand-in for Session: records what the CLI asks of it and replays scripted turns."""

    def __init__(self, turns=(), model_info=None, context_window=1000):
        self._turns = iter(turns)
        self.model_info = model_info
        self.context_window = context_window
        self.store = None
        self.conversation_id = 1
        self.asked = []
        self.calls = []

    def ask(self, text):
        self.asked.append(text)
        turn = next(self._turns)
        if isinstance(turn, Exception):
            raise turn
        yield from turn

    def load_model(self):
        self.calls.append("load_model")

    def loaded_model_info(self):
        return self.model_info

    def shutdown(self, reason):
        self.calls.append(("shutdown", reason))

    def close(self):
        self.calls.append("close")

    def pending_writes(self):
        return self.store.pending_writes(self._vault_scope)

    def resolve_write(self, attempt_id, state):
        self.store.resolve_write(attempt_id, self._vault_scope, state)


@pytest.fixture
def fake_session(monkeypatch, tmp_path):
    def install(**kwargs):
        s = FakeSession(**kwargs)
        s.store = ConversationStore(tmp_path / "conversation.db")
        monkeypatch.setattr(cli.Session, "start", classmethod(lambda cls: s))
        return s

    return install


def fake_input(lines):
    """Feeds `lines` one at a time, then raises EOFError like input() does at EOF."""
    remaining = iter(lines)

    def _input(*_):
        try:
            return next(remaining)
        except StopIteration:
            raise EOFError

    return _input


def fake_input_recording_prompts(lines, prompts):
    """Like fake_input, but also records each prompt string input() was called with."""
    remaining = iter(lines)

    def _input(prompt=""):
        prompts.append(prompt)
        try:
            return next(remaining)
        except StopIteration:
            raise EOFError

    return _input


def test_context_usage_percent_is_zero_for_an_empty_conversation(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()

    assert cli._context_usage_percent(store, conversation_id, context_window=1000) == 0


def test_context_usage_percent_reflects_full_context_window_not_the_reserved_budget(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    # 400 of a 1000-token window = 40%, even though the 80% response-reserved
    # budget (800) is what actually bounds which history gets included.
    store.add_message(conversation_id, "user", "hi", tokens_used=400)

    assert cli._context_usage_percent(store, conversation_id, context_window=1000) == 40


def test_context_usage_percent_is_capped_at_100(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    store.add_message(conversation_id, "user", "hi", tokens_used=5000)

    assert cli._context_usage_percent(store, conversation_id, context_window=1000) == 100


def test_context_usage_percent_uses_only_the_latest_turn_not_a_sum(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    # Each user row's tokens_used is already the FULL prompt size Ollama saw for
    # that call (history + tool schema + everything) - summing rows would
    # double-count prior turns' content. Only the latest one should matter.
    store.add_message(conversation_id, "user", "first", tokens_used=300)
    store.add_message(conversation_id, "nuru", "reply", tokens_used=50)
    store.add_message(conversation_id, "user", "second", tokens_used=350)

    assert cli._context_usage_percent(store, conversation_id, context_window=1000) == 35


def test_main_shows_context_usage_percent_in_the_input_prompt(monkeypatch, fake_session):
    fake_session(turns=[[]])
    prompts = []
    monkeypatch.setattr(builtins, "input", fake_input_recording_prompts(["hi"], prompts))

    cli.main()

    assert prompts[0] == "[user: 0%] "


def test_main_sends_what_you_type_to_the_session(monkeypatch, fake_session):
    s = fake_session(turns=[[], []])
    monkeypatch.setattr(builtins, "input", fake_input(["first", "second"]))

    cli.main()

    assert s.asked == ["first", "second"]


def test_main_echoes_response_prefixed_with_nuru(monkeypatch, capsys, fake_session):
    fake_session(turns=[[Text("Hi"), Text(" there")]])
    monkeypatch.setattr(builtins, "input", fake_input(["hello"]))

    cli.main()

    assert "[nuru] Hi there\n" in capsys.readouterr().out


def test_main_does_not_print_nuru_prefix_for_empty_response(monkeypatch, capsys, fake_session):
    fake_session(turns=[[]])
    monkeypatch.setattr(builtins, "input", fake_input(["hi"]))

    cli.main()

    assert "[nuru]" not in capsys.readouterr().out


def test_main_prints_tool_calls_between_stretches_of_the_reply(monkeypatch, capsys, fake_session):
    fake_session(turns=[[Text("Let me look."), ToolCall("get_temperature", {"city": "Berlin"}), Text("It's 17C")]])
    monkeypatch.setattr(builtins, "input", fake_input(["what's the weather in Berlin?"]))

    cli.main()

    assert (
        "[nuru] Let me look.\n"
        "[tool] get_temperature({'city': 'Berlin'})\n"
        "[nuru] It's 17C\n"
    ) in capsys.readouterr().out


def test_main_says_when_a_silent_call_is_being_asked_again_with_thinking_off(monkeypatch, capsys, fake_session):
    fake_session(turns=[[Thinking("the answer is 4"), RetryWithoutThinking(), Text("4")]])
    monkeypatch.setattr(builtins, "input", fake_input(["what is 2+2?"]))

    cli.main()

    assert (
        "[thinking] the answer is 4\n"
        "[retry] No reply was written - asking again with thinking off.\n"
        "[nuru] 4\n"
    ) in capsys.readouterr().out


def test_main_prints_what_the_model_thought_on_its_own_line(monkeypatch, capsys, fake_session):
    fake_session(turns=[[Text("Let me look."), Thinking("the vault should have it"), Text("Found it.")]])
    monkeypatch.setattr(builtins, "input", fake_input(["find x"]))

    cli.main()

    assert (
        "[nuru] Let me look.\n"
        "[thinking] the vault should have it\n"
        "[nuru] Found it.\n"
    ) in capsys.readouterr().out


def test_main_prints_a_failure_as_an_error_and_keeps_going(monkeypatch, capsys, fake_session):
    s = fake_session(turns=[[Failure("request to the model failed: timed out")], [Text("back")]])
    monkeypatch.setattr(builtins, "input", fake_input(["hello", "still here?"]))

    cli.main()  # must not raise

    out = capsys.readouterr().out
    assert "[error] request to the model failed: timed out" in out
    assert "[nuru] back" in out
    assert s.asked == ["hello", "still here?"]


def test_main_ends_the_line_before_an_error_that_interrupts_a_reply(monkeypatch, capsys, fake_session):
    fake_session(turns=[[Text("partial"), Failure("boom")]])
    monkeypatch.setattr(builtins, "input", fake_input(["hi"]))

    cli.main()

    assert "[nuru] partial\n[error] boom\n" in capsys.readouterr().out


def test_main_exits_cleanly_on_eof_without_shutting_the_model_down(monkeypatch, fake_session):
    s = fake_session()
    monkeypatch.setattr(builtins, "input", fake_input([]))

    cli.main()  # must not raise

    assert s.calls == ["close"]


def test_main_exit_command_shuts_down_without_asking_the_model(monkeypatch, fake_session):
    s = fake_session()
    monkeypatch.setattr(builtins, "input", fake_input(["/exit"]))

    cli.main()

    assert s.asked == []
    assert s.calls == [("shutdown", "/exit"), "close"]


@pytest.mark.parametrize("command", ["/exit", "/bye", "/end", "/quit"])
def test_main_exit_aliases_all_shut_down(monkeypatch, fake_session, command):
    s = fake_session()
    monkeypatch.setattr(builtins, "input", fake_input([command]))

    cli.main()

    assert ("shutdown", "/exit") in s.calls


def test_main_releases_the_session_even_when_a_turn_raises(monkeypatch, fake_session):
    s = fake_session(turns=[RuntimeError("tool blew up")])
    monkeypatch.setattr(builtins, "input", fake_input(["hi"]))

    with pytest.raises(RuntimeError, match="tool blew up"):
        cli.main()

    assert s.calls == ["close"]


def test_main_show_command_prints_model_info_when_loaded(monkeypatch, capsys, fake_session):
    s = fake_session(model_info={"family": "qwen3", "context_window": 4096})
    monkeypatch.setattr(builtins, "input", fake_input(["/show"]))

    cli.main()

    out = capsys.readouterr().out
    assert "qwen3" in out
    assert "4096" in out
    assert s.asked == []


def test_main_show_command_prints_no_model_loaded_when_not_loaded(monkeypatch, capsys, fake_session):
    fake_session(model_info=None)
    monkeypatch.setattr(builtins, "input", fake_input(["/show"]))

    cli.main()

    assert "no model loaded" in capsys.readouterr().out


def test_main_load_command_loads_the_model_without_asking_it_anything(monkeypatch, fake_session):
    s = fake_session()
    monkeypatch.setattr(builtins, "input", fake_input(["/load"]))

    cli.main()

    assert s.calls == ["load_model", "close"]
    assert s.asked == []


def test_main_help_command_lists_commands_without_asking_the_model(monkeypatch, capsys, fake_session):
    s = fake_session()
    monkeypatch.setattr(builtins, "input", fake_input(["/help"]))

    cli.main()

    out = capsys.readouterr().out
    assert "/load" in out
    assert "/show" in out
    assert "/exit" in out
    assert s.asked == []


@pytest.fixture
def typed_in_time(monkeypatch):
    monkeypatch.setattr(cli, "_input_ready", lambda timeout: True)


@pytest.mark.parametrize("reply, approved", [("y", True), ("YES", True), ("", False), ("n", False), ("sure", False)])
def test_a_write_is_confirmed_only_by_a_yes(monkeypatch, typed_in_time, reply, approved):
    monkeypatch.setattr("builtins.input", lambda *prompt: reply)
    assert questions.is_yes(cli.answer(questions.write("write_note", {"note": "x", "content": "y"}))) is approved


def test_no_answer_is_none(monkeypatch, typed_in_time):
    def eof(*prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert cli.answer(questions.write("write_note", {})) is None


def test_a_timed_question_says_how_long_it_waits_and_gives_up_when_nothing_is_typed(monkeypatch, capsys):
    waited = []
    monkeypatch.setattr(cli, "_input_ready", lambda timeout: waited.append(timeout) or False)
    monkeypatch.setattr("builtins.input", lambda *prompt: pytest.fail("nothing was typed to read"))

    assert cli.answer(questions.write("write_note", {"note": "x"})) is None

    assert waited == [10]
    out = capsys.readouterr().out
    assert "Allow it? (No in 10 s)" in out
    assert "No answer in 10 s - taken as No." in out


def test_where_stdin_cannot_be_waited_on_a_timed_question_waits_for_the_line(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", object())
    assert cli._input_ready(10) is True


def test_enter_takes_the_recommended_answer(monkeypatch, capsys):
    prompts = []
    monkeypatch.setattr("builtins.input", fake_input_recording_prompts([""], prompts))
    assert cli.answer(questions.yes_no("Save it anyway?", recommend="No")) == ["No"]
    monkeypatch.setattr("builtins.input", fake_input_recording_prompts([""], prompts))
    assert cli.answer(questions.yes_no("Use the new figure?", recommend="Yes")) == ["Yes"]
    assert prompts == ["[answer] [y/N] ", "[answer] [Y/n] "]
    assert "[ask] Save it anyway?" in capsys.readouterr().out


QUESTION = questions.from_tool({"question": "Which reporting rate?", "kind": "single", "options": [
    {"label": "4 reports/h", "description": "the revised note", "recommended": True}, {"label": "6 reports/h"}]})


def test_options_are_numbered_with_the_recommended_one_marked(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", fake_input(["2"]))
    assert cli.answer(QUESTION) == ["6 reports/h"]
    out = capsys.readouterr().out
    assert "1. 4 reports/h (recommended) - the revised note" in out
    assert "2. 6 reports/h" in out


@pytest.mark.parametrize("reply, chosen", [("", ["4 reports/h"]), ("5", ["5"]), ("one an hour", ["one an hour"])])
def test_enter_picks_the_recommended_option_and_other_words_are_your_own_answer(monkeypatch, reply, chosen):
    monkeypatch.setattr("builtins.input", fake_input([reply]))
    assert cli.answer(QUESTION) == chosen


def test_a_checkbox_question_takes_several_numbers(monkeypatch):
    question = questions.from_tool({"question": "Which notes?", "kind": "multi", "options": ["A", "B", "C"]})
    monkeypatch.setattr("builtins.input", fake_input(["3, 1,3"]))
    assert cli.answer(question) == ["C", "A"]


def test_a_single_choice_asks_again_for_a_number_out_of_range_when_it_takes_no_own_answer(monkeypatch, capsys):
    question = questions.Question("Which?", questions.SINGLE, (questions.Option("A"), questions.Option("B")))
    monkeypatch.setattr("builtins.input", fake_input(["1,2", "7", "2"]))
    assert cli.answer(question) == ["B"]
    assert capsys.readouterr().out.count("isn't one of the options") == 2


def test_main_answers_a_question_the_turn_ended_on_and_sends_the_answer(monkeypatch, capsys, fake_session):
    s = fake_session(turns=[[Text("Two notes disagree."), Ask(QUESTION)], [Text("At 4 reports/h: 3.47 years.")]])
    monkeypatch.setattr(builtins, "input", fake_input(["battery life?", "1"]))
    cli.main()
    assert s.asked == ["battery life?", "4 reports/h"]
    assert "3.47 years" in capsys.readouterr().out


def test_main_goes_back_to_the_prompt_when_a_question_is_skipped(monkeypatch, fake_session):
    question = questions.Question("Which?", questions.SINGLE, (questions.Option("A"), questions.Option("B")))
    s = fake_session(turns=[[Ask(question)], [Text("ok")]])
    monkeypatch.setattr(builtins, "input", fake_input(["go", "", "something else"]))
    cli.main()
    assert s.asked == ["go", "something else"]


def test_main_hands_the_session_its_question_renderer(monkeypatch, fake_session):
    s = fake_session()
    monkeypatch.setattr(builtins, "input", fake_input([]))
    cli.main()
    assert s.answer_question is cli.answer


def test_write_reconciliation_command_does_not_send_a_model_prompt(fake_session, monkeypatch, capsys):
    s = fake_session()
    s._vault_scope = 'scratch'
    cid = s.store.create_conversation()
    attempt = s.store.begin_write(cid, 't-1', 'scratch', 'write_note', {'content': 'line'})
    monkeypatch.setattr(builtins, 'input', fake_input(['/writes', f'/resolve-write {attempt} completed', '/writes']))
    cli.main()
    assert not s.asked and not s.store.pending_writes('scratch')
    assert 'No write was executed.' in capsys.readouterr().out


def test_main_streams_the_reasoning_on_one_line_and_does_not_repeat_it(monkeypatch, capsys, fake_session):
    fake_session(turns=[[ThinkingChunk("the vault "), ThinkingChunk("should have it"), Thinking("the vault should have it"),
                         ToolCall("search", {"query": "x"}), ThinkingChunk("found"), Text("Found it.")]])
    monkeypatch.setattr(builtins, "input", fake_input(["find x"]))

    cli.main()

    assert (
        "[thinking] the vault should have it\n"
        "[tool] search({'query': 'x'})\n"
        "[thinking] found\n"
        "[nuru] Found it.\n"
    ) in capsys.readouterr().out
