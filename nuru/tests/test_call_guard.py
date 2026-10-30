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
    assert call_guard.read_step('{"tool": "calculate", "arguments": {"expression": "2340 * 0.175"}}', schema, TOOLS) == {
        "tool_call": {"function": {"name": "calculate", "arguments": {"expression": "2340 * 0.175"}}}}
    assert call_guard.read_step('{"answer": "Hello!"}', schema, TOOLS) == {"answer": "Hello!"}
    explained = 'search("router") finds every note that mentions the router.'  # an explanation, asked for
    assert call_guard.read_step(json.dumps({"answer": explained}), schema, TOOLS) == {"answer": explained}
    for broken in ("", "not json", '{"tool": "search", "arguments": {}}', "[1]", '{"tool": "search"',
                   '{"answer": "   "}',  # blank is no reply
                   '{"answer": "search(\\"router\\")"}',  # the same call written out again
                   '{"answer": "calculate(expression=\\"2 * 3\\") -> 6"}'):
        assert call_guard.read_step(broken, schema, TOOLS) is None, broken


def test_a_native_call_in_reply_to_the_forced_step_is_taken_if_its_arguments_fit():
    schema = call_guard.step_schema(TOOLS)
    call = {"function": {"name": "search", "arguments": {"query": "router"}}}
    assert call_guard.native_step(call, schema, TOOLS) == {"tool_call": call}
    for bad in ({"function": {"name": "search", "arguments": {}}}, {"function": {"name": "remember", "arguments": {}}},
                {"function": {"name": "search"}}, {}):
        assert call_guard.native_step(bad, schema, TOOLS) is None


@pytest.mark.parametrize("text,bare", [
    ('search("router")', True), ('search("router") ', True), ('calculate(expression="2 * 3") -> 6', True),
    ('```search("a (b) c")```', True), ('search{query: "router"}', True), ('tags()', True),
    ('statistics(numbers=[1, 2], x=[0, 1])', True), ('statistics(numbers=[1, 2]) gives the mean.', False),
    ('search(query="router"', True),  # cut off before it closed
    ('search with "car service"', True), ('{"name": "search", "arguments": {"query": "x"}}', True),
    ('search("router") finds every note that mentions it.', False), ('search with "x"\nand then read the best match', False),
    ('search(")") is fine', False), ("The router note says hi.", False), ('search(\'it\\\'s\')', True),
])
def test_a_bare_call_is_nothing_but_the_call(text, bare):
    assert call_guard.bare_call(text, TOOLS) is bare
    assert not call_guard.bare_call(text, [])


@pytest.mark.parametrize("text,thinking,tokens,expected", [
    ('search("router")', "", 4, call_guard.TEXT_CALL),
    ('search("router")', "it needs a search", 4, call_guard.TEXT_CALL),
    ("", "", 17, call_guard.EMPTY_REPLY),  # tokens, and nothing came of them
    ("  \n", "", 3, call_guard.EMPTY_REPLY),
    ("", "only reasoning", 17, None),  # the silent-turn retry's case
    ("", "", 0, None),  # nothing was written at all
    ("The router note says hi.", "", 9, None),
])
def test_what_makes_a_step_be_asked_again(text, thinking, tokens, expected):
    assert call_guard.trigger(text, TOOLS, thinking, tokens) == expected


def test_a_reply_that_reads_as_a_call_is_held_to_its_end_however_long():
    long_call = 'write_note(content="' + "x" * (2 * call_guard.HOLD_CHARS) + '")'
    assert call_guard.may_be_call(long_call, TOOLS)
    assert call_guard.may_be_call(long_call + "\nand more", TOOLS)
