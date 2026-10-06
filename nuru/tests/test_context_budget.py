import pytest

from core.context_budget import ContextBudgetExceeded, available_tokens, estimate_tokens, require_fit


@pytest.mark.parametrize("text,expected", [("", 0), ("x", 1), ("xxxxx", 2), ("ü" * 4, 2), ("😀", 1)])
def test_nonempty_content_has_a_nonzero_byte_estimate(text, expected):
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
