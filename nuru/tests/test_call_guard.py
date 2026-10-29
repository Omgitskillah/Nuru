"""core.call_guard against the tools Nuru really offers: fixtures/model_tools.json is MCPTools.list_tools()
from the notes server (servers/obsidian e8e9953) and the calculator, as of 2026-10-29. The forms are
what nemotron-3.5-bolt:e4b wrote on 2026-10-29 instead of calling (scratch f22 runs)."""

import json
from pathlib import Path

import jsonschema
import pytest

from core import call_guard

TOOLS = json.loads((Path(__file__).parent / "fixtures" / "model_tools.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("text", [
    'search("car service")',
    'calculate(expression="2340 * 0.175") -> 409.5',
    'search{query: "car service"}',
    'tags()',
    'browse{}',
    'search with "car service"',
    'Action: search(query="router project")',
    'call tags{}',
    'write_note{content: "- [ ] call the dentist",type:<|"|>daily<|"|>}',
    '<|tool_call>call:search{query:<|"|>car<|"|>}<tool_call|>',
    '```\nsearch("router")\n```',
    '{"name": "search", "arguments": {"query": "router"}}',
    'Search("router")',
])
def test_calls_written_as_text_are_recognised(text):
    assert call_guard.looks_like_call(text, TOOLS)


@pytest.mark.parametrize("text", [
    "The car is due for its service on 14 March.",
    "Search results show two notes about the router.",
    "17.5% of 2,340 is 409.5.",
    "I searched your notes (search) and found nothing.",
    "",
    'remember("x")',  # not a tool Nuru offers
])
def test_replies_are_not_taken_for_calls(text):
    assert not call_guard.looks_like_call(text, TOOLS)


def test_no_tools_means_nothing_is_a_call():
    assert not call_guard.looks_like_call('search("x")', [])
    assert not call_guard.may_be_call("se", [])


@pytest.mark.parametrize("text", ["", "  ", "Action: ", "s", "sea", "Search", "search ", "search wi", "{", '{"name": "se', "statistics"])
def test_the_start_of_a_reply_is_held_while_it_may_still_be_a_call(text):
    assert call_guard.may_be_call(text, TOOLS)


@pytest.mark.parametrize("text", ["The", "Sure", "search results", "searching", "17.5", "tags are", "Hello\nthere",
                                  "x" * (call_guard.HOLD_CHARS + 1)])
def test_a_reply_is_shown_once_it_clearly_isnt_a_call(text):
    assert not call_guard.may_be_call(text, TOOLS)


def test_the_schema_admits_each_tool_with_arguments_that_fit_it_or_an_answer():
    schema = call_guard.step_schema(TOOLS)
    jsonschema.Draft202012Validator.check_schema(schema)
    ok = [{"tool": "search", "arguments": {"query": "router"}},
          {"tool": "tags", "arguments": {}},
          {"tool": "write_note", "arguments": {"type": "daily", "content": "- call the dentist"}},
          {"tool": "statistics", "arguments": {"numbers": [1, 3], "x": [0, 1], "reference_line": {"slope": 2, "intercept": 1}}},
          {"answer": "Hello!"}]
    for step in ok:
        jsonschema.validate(step, schema)
    bad = [{"tool": "search", "arguments": {}},  # its required query
           {"tool": "search", "arguments": {"query": "x", "limit": 3}},  # a field it doesn't take
           {"tool": "tags", "arguments": {"tool": "tags", "arguments": {}}},  # what thinking nemotron nested
           {"tool": "write_note", "arguments": {"type": "daily"}},  # no content: REQUIRED
           {"tool": "remember", "arguments": {}},
           {"answer": ""},
           {"tool": "search", "arguments": {"query": "x"}, "answer": "both"}]
    for step in bad:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(step, schema)


def test_the_schema_resolves_definitions_from_its_root_and_leaves_the_tools_alone():
    before = json.dumps(TOOLS, sort_keys=True)
    schema = call_guard.step_schema(TOOLS)
    assert "ReferenceLine" in schema["$defs"]  # statistics' $ref "#/$defs/ReferenceLine" must resolve from the root
    assert json.dumps(TOOLS, sort_keys=True) == before
    bare = call_guard.step_schema([{"type": "function", "function": {"name": "ping"}}])
    assert "$defs" not in bare
    jsonschema.validate({"tool": "ping", "arguments": {}}, bare)


def test_a_reply_under_the_schema_becomes_a_call_or_an_answer():
    schema = call_guard.step_schema(TOOLS)
    assert call_guard.read_step('{"tool": "calculate", "arguments": {"expression": "2340 * 0.175"}}', schema) == {
        "tool_call": {"function": {"name": "calculate", "arguments": {"expression": "2340 * 0.175"}}}}
    assert call_guard.read_step('{"answer": "Hello!"}', schema) == {"answer": "Hello!"}
    for broken in ("", "not json", '{"tool": "search", "arguments": {}}', "[1]"):
        assert call_guard.read_step(broken, schema) is None
