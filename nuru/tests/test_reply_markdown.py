import pytest

from core.reply_markdown import (
    BREAK,
    TOOL_CALL_LIMIT,
    Code,
    Prose,
    Reply,
    escape_md,
    format_tool_call,
    qt_safe,
    split_fences,
)
from core import questions
from core.session import Ask, Failure, RetryWithoutThinking, Text, Thinking, ThinkingChunk, ToolCall


def reply_of(*events):
    reply = Reply()
    for event in events:
        reply.add(event)
    return reply


def test_an_empty_reply_is_empty_markdown():
    assert Reply().markdown == ""


def test_text_chunks_join_into_one_piece_of_text():
    assert reply_of(Text("Hel"), Text("lo "), Text("there")).markdown == "Hello there"


def test_a_tool_call_leaves_a_break_where_it_happened_but_nothing_of_itself():
    # The call itself is the reply header's to show; here it only sets what was said around it apart.
    reply = reply_of(Text("Let me check."), ToolCall("search_notes", {"query": "budget"}), Text("Found it."))

    assert reply.markdown == "Let me check." + BREAK + "Found it."
    assert "search_notes" not in reply.markdown
    assert "[nuru]" not in reply.markdown and "[tool]" not in reply.markdown


def test_thinking_leaves_the_reply_alone_the_way_a_tool_call_does():
    reply = reply_of(Text("Let me check."), Thinking("the vault should have it"), Text("Found it."))

    assert reply.markdown == "Let me check." + BREAK + "Found it."
    assert "the vault should have it" not in reply.markdown


def test_a_retry_leaves_the_reply_alone_the_way_thinking_does():
    # The header says the turn was retried; the body stays the model's own words.
    reply = reply_of(Text("Let me check."), RetryWithoutThinking(), Text("Found it."))

    assert reply.markdown == "Let me check." + BREAK + "Found it."
    assert "retr" not in reply.markdown


def test_the_break_is_a_thematic_break_with_blank_lines_so_it_cant_make_a_heading():
    markdown = reply_of(Text("above"), ToolCall("t", {}), Text("below")).markdown

    assert markdown == "above\n\n---\n\nbelow"


def test_no_break_before_a_first_tool_call_or_after_a_last_one():
    # A model call that only asks for a tool has no text, and the reply may end on the tool
    # still running: neither should leave a dangling break at either end.
    assert reply_of(ToolCall("t", {})).markdown == ""
    assert reply_of(ToolCall("a", {}), Text("done")).markdown == "done"
    assert reply_of(Text("working"), ToolCall("a", {})).markdown == "working"


def test_consecutive_tool_calls_leave_only_one_break_between_the_text_around_them():
    markdown = reply_of(Text("looking"), ToolCall("a", {}), ToolCall("b", {}), Text("done")).markdown

    assert markdown == "looking" + BREAK + "done"


def test_text_after_a_tool_call_starts_a_new_piece_rather_than_extending_the_old_one():
    reply = reply_of(Text("before"), ToolCall("t", {}), Text("after"))

    assert reply.markdown.count("---") == 1


def test_whitespace_around_pieces_does_not_leave_stray_breaks():
    # Models commonly open with a newline or two, and a piece can be nothing but whitespace.
    reply = reply_of(Text("\n\nHello\n"), ToolCall("t", {}), Text("  \n"), ToolCall("u", {}), Text("\nBye\n\n"))

    assert reply.markdown == "Hello" + BREAK + "Bye"


def test_a_streaming_reply_grows_without_rewriting_what_was_already_shown():
    reply = Reply()
    seen = []
    for event in [Text("Hel"), Text("lo"), ToolCall("t", {}), Text("Wor"), Text("ld")]:
        reply.add(event)
        seen.append(reply.markdown)

    assert seen[-1] == "Hello" + BREAK + "World"
    for earlier, later in zip(seen, seen[1:]):
        assert later.startswith(earlier)


def test_a_failure_is_shown_as_an_error_piece():
    markdown = reply_of(Text("partial"), Failure("request to the model failed: timed out")).markdown

    assert markdown == "partial" + BREAK + "**Error:** request to the model failed: timed out"


def test_error_text_is_shown_literally_and_its_lines_become_paragraphs():
    reply = Reply()
    reply.add_error("bad `thing` in *file_name*\nsecond line")

    assert reply.markdown == "**Error:** bad \\`thing\\` in \\*file\\_name\\*\n\nsecond line"


def test_format_tool_call_is_the_call_written_out_as_one_line():
    assert format_tool_call("search_notes", {"query": "budget"}) == "search_notes({'query': 'budget'})"
    assert format_tool_call("no_args", {}) == "no_args({})"


def test_format_tool_call_keeps_a_multiline_argument_on_one_line():
    call = format_tool_call("create_note", {"content": "line one\n\nline two"})

    assert "\n" not in call


def test_format_tool_call_cuts_a_long_call_short():
    call = format_tool_call("create_note", {"content": "word " * 500})

    assert call.startswith("create_note({'content': 'word word")
    assert call.endswith("…")
    assert len(call) == TOOL_CALL_LIMIT


def test_format_tool_call_leaves_a_call_at_the_limit_alone():
    name = "t"
    padding = TOOL_CALL_LIMIT - len("t({'a': ''})")
    call = format_tool_call(name, {"a": "x" * padding})

    assert "…" not in call
    assert len(call) == TOOL_CALL_LIMIT


def test_escape_md_escapes_markdown_syntax():
    assert escape_md("*a* _b_ `c` [d] #e |f| ~g~ <h> {i} \\j") == (
        "\\*a\\* \\_b\\_ \\`c\\` \\[d\\] \\#e \\|f\\| \\~g\\~ \\<h\\> \\{i\\} \\\\j"
    )


def test_escape_md_leaves_plain_text_alone():
    assert escape_md("Just some words, 123.") == "Just some words, 123."


def test_split_fences_leaves_plain_markdown_as_one_prose_part():
    assert split_fences("# Title\n\nSome text") == [Prose("# Title\n\nSome text")]


def test_split_fences_pulls_a_code_block_out_from_between_prose():
    parts = split_fences("Before\n\n```python\nprint(1)\nprint(2)\n```\n\nAfter")

    assert parts == [
        Prose("Before\n\n"),
        Code("python", "print(1)\nprint(2)"),
        Prose("\n\nAfter"),
    ]


def test_split_fences_gives_a_fence_with_no_language_an_empty_one():
    assert split_fences("```\nplain\n```") == [Code("", "plain")]


def test_split_fences_treats_a_fence_still_streaming_as_open():
    assert split_fences("Intro\n\n```python\nprint(") == [Prose("Intro\n\n"), Code("python", "print(")]


def test_split_fences_does_not_treat_a_half_typed_fence_as_code_yet():
    # No newline after the backticks: the language line hasn't finished arriving.
    assert split_fences("Intro\n\n```pyt") == [Prose("Intro\n\n```pyt")]


def test_split_fences_handles_several_blocks_and_drops_empty_prose_between_them():
    assert split_fences("```a\none\n```\n```b\ntwo\n```") == [Code("a", "one"), Code("b", "two")]


def test_split_fences_ignores_an_empty_string():
    assert split_fences("") == []


def test_a_rule_after_a_code_block_survives_being_split_out_of_the_fence():
    # A tool call right after code: the break has to land in prose, not inside the code box.
    markdown = reply_of(Text("Try:\n\n```sh\nls\n```"), ToolCall("t", {}), Text("Done.")).markdown

    parts = split_fences(markdown)

    assert parts[1] == Code("sh", "ls")
    assert isinstance(parts[2], Prose) and "---" in parts[2].text and "Done." in parts[2].text


def test_a_question_leaves_the_reply_alone_for_its_card_to_show():
    reply = Reply()
    reply.add(Text("The notes disagree."))
    reply.add(Ask(questions.yes_no("Use the revised one?")))
    assert reply.markdown == "The notes disagree."


def test_streamed_reasoning_leaves_the_reply_alone_like_the_whole_does():
    reply = Reply()
    reply.add(ThinkingChunk("hmm"))
    reply.add(Text("Answer."))
    assert reply.markdown == "Answer."


@pytest.mark.parametrize("text, safe", [
    ("| x<br>y | z |", "| x<br/>y | z |"),
    ("a<BR >b<br/>c<br />d", "a<br/>b<br/>c<br />d"),
    ("keep `x<br>y` literal, fix this<br>one", "keep `x<br>y` literal, fix this<br/>one"),
    ("``code with ` and <br>`` then<br>", "``code with ` and <br>`` then<br/>"),
    ("no breaks here", "no breaks here"),
])
def test_qt_safe_closes_br_tags_outside_inline_code(text, safe):
    assert qt_safe(text) == safe
