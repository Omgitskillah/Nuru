import builtins

from core import cli


def fake_input(lines):
    """Feeds `lines` one at a time, then raises EOFError like input() does at EOF."""
    remaining = iter(lines)

    def _input(*_):
        try:
            return next(remaining)
        except StopIteration:
            raise EOFError

    return _input


def fake_chat(chunks):
    def _chat(model, messages, think=True):
        yield from chunks

    return _chat


def test_main_echoes_response_prefixed_with_nuru(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", fake_input(["hello"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    monkeypatch.setattr(cli, "chat", fake_chat(["Hi", " there", {"prompt_tokens": 1, "completion_tokens": 2}]))

    cli.main()

    out = capsys.readouterr().out
    assert "[nuru] Hi there" in out


def test_main_sends_each_line_as_its_own_message(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input(["first", "second"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)

    sent = []

    def recording_chat(model, messages, think=True):
        sent.append(messages)
        return iter([])

    monkeypatch.setattr(cli, "chat", recording_chat)

    cli.main()

    assert sent == [
        [{"role": "user", "content": "first"}],
        [{"role": "user", "content": "second"}],
    ]


def test_main_exits_cleanly_on_eof(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input([]))

    cli.main()  # must not raise


def test_main_checks_server_and_model_before_each_send(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input(["hi"]))

    calls = []
    monkeypatch.setattr(cli, "ensure_server_running", lambda: calls.append("server"))
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: calls.append(("model", model)))
    monkeypatch.setattr(cli, "chat", fake_chat([]))

    cli.main()

    assert calls == ["server", ("model", cli.MODEL)]


def test_main_passes_think_setting_to_chat(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input(["hi"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    monkeypatch.setattr(cli, "THINK", False)

    seen = {}

    def recording_chat(model, messages, think=True):
        seen["think"] = think
        return iter([])

    monkeypatch.setattr(cli, "chat", recording_chat)

    cli.main()

    assert seen["think"] is False


def test_main_does_not_print_nuru_prefix_for_empty_response(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", fake_input(["hi"]))
    monkeypatch.setattr(cli, "ensure_server_running", lambda: None)
    monkeypatch.setattr(cli, "ensure_model_loaded", lambda model: None)
    monkeypatch.setattr(cli, "chat", fake_chat([{"prompt_tokens": 1, "completion_tokens": 0}]))

    cli.main()

    assert "[nuru]" not in capsys.readouterr().out
