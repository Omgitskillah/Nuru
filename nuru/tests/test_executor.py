import json
import re

import pytest

from core import executor, session
from core.session import Text, ToolCall
from test_session import FakeMCPTools, start_session


def tool(name, properties, required=()):
    return {"type": "function", "function": {"name": name, "description": "",
            "parameters": {"type": "object", "properties": properties, "required": list(required)}}}


TOOLS = [
    tool("search", {"query": {"type": "string"}}, ["query"]),
    tool("read_note", {"name": {"type": "string"}}, ["name"]),
    tool("calculate", {"expression": {"type": "string"}}, ["expression"]),
    tool("statistics", {"numbers": {"type": "array", "items": {"type": "number"}}}, ["numbers"]),
    tool("write_note", {"note": {"type": "string"}, "content": {"type": "string"}, "type": {"type": "string"}}, ["content"]),
]
SEARCH_RESULT = json.dumps({"query": "budget", "matches": [{"path": "nuru/march-budget.md", "match": "exact"},
                                                           {"path": "nuru/april-budget.md", "match": "meaning"}]})


# --- the pure parts ---


def test_resolve_puts_a_whole_reference_in_as_its_value_and_one_inside_text_as_text():
    values = {"s1": "nuru/a.md", "s2": [1, 2], "s3": "42.5"}

    args = executor.resolve({"name": "$s1", "numbers": "$s2", "content": "Total: $s3"}, values)

    assert args == {"name": "nuru/a.md", "numbers": [1, 2], "content": "Total: 42.5"}


def test_resolve_raises_for_a_step_that_has_no_output():
    with pytest.raises(KeyError):
        executor.resolve({"name": "$s9"}, {})


def test_step_value_takes_the_part_a_later_step_uses():
    assert executor.step_value("calculate", '{"ok":true,"result":"42.5","schema_version":1}', True) == "42.5"
    assert executor.step_value("write_note", '{"done":"Added to x.","path":"x.md"}', True) == "Added to x."
    assert executor.step_value("read_note", "---\nid: 1\n---\nBody", True) == "---\nid: 1\n---\nBody"
    assert executor.step_value("statistics", '{"ok":true,"schema_version":1,"mean":2}', True) == {"mean": 2}


def test_tool_failed_sees_a_math_tools_ok_false_inside_a_normal_result():
    assert executor.tool_failed("calculate", '{"ok":false,"hint":"not arithmetic"}', True) == "not arithmetic"
    assert executor.tool_failed("calculate", '{"ok":true,"result":"1"}', True) is None
    assert executor.tool_failed("search", "boom", False) == "boom"


def test_candidates_are_every_path_a_result_names_once_in_order():
    assert executor.candidates(SEARCH_RESULT) == ["nuru/march-budget.md", "nuru/april-budget.md"]
    assert executor.candidates("plain note text") == []


def test_a_draft_takes_the_type_of_the_argument_it_fills_whole():
    steps = [{"step": "s1", "think": "draft"}, {"step": "s2", "tool": "statistics", "with": {"numbers": "$s1"}},
             {"step": "s3", "think": "draft"}, {"step": "s4", "tool": "write_note", "with": {"content": "Total: $s3"}}]

    assert executor.consumer_schema("s1", steps, TOOLS) == {"type": "array", "items": {"type": "number"}}
    assert executor.consumer_schema("s3", steps, TOOLS) == {"type": "string"}


# --- Session.run_plan ---


def scripted_chat(replies):
    """A fake chat that answers each call with the next reply, recording what each call was given."""
    calls = []

    def _chat(model, messages, num_ctx=8192, think=True, tools=None, fmt=None):
        calls.append({"messages": messages, "fmt": fmt, "tools": tools})
        yield replies[len(calls) - 1]
        yield {"prompt_tokens": 1, "completion_tokens": 1, "stop_reason": "stop"}

    return _chat, calls


BUDGET_PLAN = {"steps": [
    {"step": "s1", "tool": "search", "with": {"query": "March budget"}},
    {"step": "s2", "think": "pick", "uses": ["s1"], "want": "the March budget note"},
    {"step": "s3", "tool": "read_note", "with": {"name": "$s2"}},
    {"step": "s4", "think": "draft", "uses": ["s3"], "want": "an expression summing the grocery amounts"},
    {"step": "s5", "tool": "calculate", "with": {"expression": "$s4"}},
    {"step": "s6", "tool": "write_note", "with": {"type": "daily", "content": "- Grocery total: $s5"}},
    {"step": "s7", "think": "answer", "uses": ["s5", "s6"], "want": "the total and where it was written"},
]}


def run(monkeypatch, replies, tool_results, plan=BUDGET_PLAN):
    chat, calls = scripted_chat(replies)
    tools = FakeMCPTools(tools=TOOLS, tool_results=tool_results)
    s = start_session(monkeypatch, chat=chat, tools=tools)
    monkeypatch.setattr(session, "THINK", False)
    events = list(s.run_plan("Add up my March groceries into today's daily note.", plan))
    return events, tools.calls, calls


def test_run_plan_runs_the_tools_itself_with_each_reference_filled_in(monkeypatch):
    events, tool_calls, model_calls = run(
        monkeypatch,
        ['{"choice": "nuru/march-budget.md"}', '{"value": "12.5 + 30"}', "42.5 on groceries, added to today's daily."],
        {"search": SEARCH_RESULT, "read_note": "Groceries: 12.50, 30.00",
         "calculate": '{"ok":true,"result":"42.5","schema_version":1}',
         "write_note": '{"done":"Added to today\'s daily note.","path":"nuru/daily.md"}'},
    )

    assert tool_calls == [
        ("search", {"query": "March budget"}),
        ("read_note", {"name": "nuru/march-budget.md"}),
        ("calculate", {"expression": "12.5 + 30"}),
        ("write_note", {"type": "daily", "content": "- Grocery total: 42.5"}),
    ]
    assert [e for e in events if isinstance(e, Text)] == [Text("42.5 on groceries, added to today's daily.")]
    assert len([e for e in events if isinstance(e, ToolCall)]) == 4
    # The model is never offered a tool, and a pick can only be one of the paths the search named.
    assert all(call["tools"] is None for call in model_calls)
    assert model_calls[0]["fmt"]["properties"]["choice"]["enum"] == ["nuru/march-budget.md", "nuru/april-budget.md", "none"]
    # The model never sees a step's name, so it can't write one where a value belongs ("30400 / s5").
    assert not any(re.search(r"\bs\d\b", call["messages"][-1]["content"]) for call in model_calls)


def test_a_pick_of_none_stops_the_plan_before_any_write_and_the_answer_says_why(monkeypatch):
    events, tool_calls, model_calls = run(
        monkeypatch, ['{"choice": "none"}', "I couldn't find a March budget note."], {"search": SEARCH_RESULT},
    )

    assert tool_calls == [("search", {"query": "March budget"})]
    assert "stopped early" in model_calls[-1]["messages"][-1]["content"]
    assert [e for e in events if isinstance(e, Text)] == [Text("I couldn't find a March budget note.")]


def test_a_rejected_expression_is_drafted_again_once_and_calculated_again(monkeypatch):
    results = iter(['{"ok":false,"hint":"not arithmetic"}', '{"ok":true,"result":"42.5"}'])
    tools = {"search": SEARCH_RESULT, "read_note": "Groceries: 12.50, 30.00", "write_note": '{"done":"Added."}'}

    class Tools(FakeMCPTools):
        def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            return next(results) if name == "calculate" else tools.get(name, "")

    chat, model_calls = scripted_chat(['{"choice": "nuru/march-budget.md"}', '{"value": "sum of groceries"}',
                                       '{"value": "12.5 + 30"}', "42.5."])
    fake = Tools(tools=TOOLS)
    s = start_session(monkeypatch, chat=chat, tools=fake)
    monkeypatch.setattr(session, "THINK", False)
    list(s.run_plan("Add up my March groceries.", BUDGET_PLAN))

    assert [c for c in fake.calls if c[0] == "calculate"] == [
        ("calculate", {"expression": "sum of groceries"}), ("calculate", {"expression": "12.5 + 30"})]
    assert "rejected" in model_calls[2]["messages"][-1]["content"]
    assert ("write_note", {"type": "daily", "content": "- Grocery total: 42.5"}) in fake.calls


def test_think_steps_are_told_the_current_date_ahead_of_the_request():
    now = "[Current date and time: Monday 2026-10-05 09:30 CEST]"
    for messages in (executor.pick_messages("When?", "the note", {}, now),
                     executor.draft_messages("When?", "the date", {}, now),
                     executor.answer_messages("When?", "the reply", [], None, now)):
        assert messages[1]["content"].startswith(now + "\n\nMy request: When?")
    assert executor.answer_messages("When?", "the reply", [], None)[1]["content"].startswith("My request: When?")
