import pytest

from core.context_budget import ContextBudgetExceeded, available_tokens, estimate_tokens, require_fit


@pytest.mark.parametrize("text,expected", [("", 0), ("x", 1), ("x" * 10, 2), ("ü" * 4, 5), ("😀", 3),
                                           ("12.5", 6), ("| a | b |\n", 6)])
def test_nonempty_content_has_a_nonzero_estimate(text, expected):
    assert estimate_tokens(text) == expected


def test_explicit_system_replaces_baked_system_and_schemas_cost_space():
    messages = [{"role": "system", "content": "executor"}, {"role": "user", "content": "hi"}]
    base = available_tokens(1000, messages)
    assert available_tokens(1000, messages, system="x" * 10000) == base
    assert available_tokens(1000, messages, tools=[{"schema": "x" * 100}]) < base
    assert available_tokens(1000, messages, fmt={"type": "object"}) < base


def test_tool_call_arguments_and_response_reserve_are_counted():
    messages = [{"role": "assistant", "tool_calls": [{"arguments": "x" * 4000}]}]
    with pytest.raises(ContextBudgetExceeded):
        require_fit(1000, messages)
    assert available_tokens(0, []) < 0


@pytest.mark.parametrize("window,expected", [(1000, 800), (8192, 6553), (16384, 14336), (32768, 30720)])
def test_response_reserve_is_capped(window, expected):
    from core.context_budget import input_budget, REQUEST_FRAMING
    assert input_budget(window) == expected
    assert available_tokens(window, []) == expected - REQUEST_FRAMING


def test_numbers_and_symbols_cost_more_than_prose_of_the_same_length():
    """A numeric table tokenizes ~3x finer than 4 bytes a token (the soak log: 14.8 KB, 11.5k tokens)."""
    row = "| 2026-09-18 11:00 | AM-01 | 3.617 | 412.4 | 22.4 | -96 | ok |\n"
    prose = "The modem stays in connected mode for most of the hour after a retry.\n"
    assert estimate_tokens(row) / len(row) > 2 * estimate_tokens(prose) / len(prose)
    assert estimate_tokens(row * 216) > len((row * 216).encode()) / 2


@pytest.mark.parametrize("filler", ["_", " ", "\t", "\u00a0", "-"])
def test_no_run_of_any_character_is_free(filler):
    """Review of #81: a request carrying 10,000 of these must not fit a 1,000-token window."""
    with pytest.raises(ContextBudgetExceeded):
        require_fit(1000, [{"role": "user", "content": filler * 10000}])


def test_base64_and_emoji_cost_what_they_measured():
    """2026-11-01, the largest of three tokenizers: 2,000 base64 characters cost 1,481 tokens, 800 emoji 2,204."""
    import base64, random
    assert estimate_tokens(base64.b64encode(random.Random(81).randbytes(1500)).decode()) >= 1481
    assert estimate_tokens("🔋⚡️📈🧪✅❌🙂🚀" * 100) >= 2204


def test_a_long_run_of_letters_is_not_one_token():
    assert estimate_tokens("a" * 400) == 80
