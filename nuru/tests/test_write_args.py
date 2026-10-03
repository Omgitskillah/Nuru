import json

import pytest

from core.write_args import ask_for_arguments, validate_arguments

KNOWN = "01M2X9M15NDE1FJYDMC0SPGRVA"
OTHER = "01M2NT6HP8THJKVENCBHBQBN5W"
WRITE_SCHEMA = {"properties": {"content": {"type": "string"},
                                "note": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                                "supersedes": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}},
                "required": ["content"], "type": "object"}


def test_valid_arguments_pass():
    result = validate_arguments("write_note", {"note": KNOWN, "content": "x"}, {KNOWN})
    assert result.arguments == {"note": KNOWN, "content": "x"} and not result.problems


def test_a_note_id_no_search_returned_is_rejected():
    result = validate_arguments("write_note", {"note": "New", "supersedes": OTHER, "content": "x"}, {KNOWN})
    assert result.arguments is None and "not returned by the last search" in result.problems[0]


def test_every_target_must_be_known():
    args = {"note": "t", "type": "correction", "author": "nuru", "targets": [KNOWN, OTHER], "content": "c"}
    assert validate_arguments("write_note", args, {KNOWN}).problems == [
        f"targets: {OTHER} was not returned by the last search"]


def test_cannot_do_is_the_way_out():
    result = validate_arguments("write_note", {"cannot_do": "no such note"}, {KNOWN})
    assert result.cannot_do == "no such note" and result.arguments is None


@pytest.mark.parametrize("answer", [
    {"note": KNOWN, "content": "x", "mode": "overwrite"},  # no such mode
    {"note": KNOWN, "content": 7},  # wrong type
    {"note": KNOWN, "content": "x", "path": "user/x.md"},  # an extra field
    {"note": 7, "content": "x"},  # wrong type
])
def test_malformed_arguments_are_rejected(answer):
    result = validate_arguments("write_note", answer, {KNOWN})
    assert result.arguments is None and result.problems


def test_the_model_is_asked_with_a_schema_limited_to_known_ids():
    seen = {}

    def chat_json(messages, fmt, think):
        seen.update(messages=messages, fmt=fmt, think=think)
        return json.dumps({"note": "Priya Raman", "content": "Prefers async on Fridays", "supersedes": KNOWN})

    history = [{"role": "user", "content": "Add a line to Priya's note"}]
    result = ask_for_arguments(chat_json, history, "write_note", WRITE_SCHEMA, [KNOWN], "low")
    assert result.arguments == {"note": "Priya Raman", "content": "Prefers async on Fridays", "supersedes": KNOWN}
    assert seen["fmt"]["anyOf"][0]["properties"]["supersedes"]["anyOf"][0]["enum"] == [KNOWN]
    assert seen["think"] == "low" and seen["messages"][-1]["content"].startswith("[Nuru] Next step: `write_note`")
    assert history == [{"role": "user", "content": "Add a line to Priya's note"}]  # the conversation is untouched


@pytest.mark.parametrize("content, problem", [("", "empty content"), ("{not json", "not JSON")])
def test_empty_or_broken_output_is_a_failure(content, problem):
    result = ask_for_arguments(lambda m, f, t: content, [], "write_note", WRITE_SCHEMA, [KNOWN], False)
    assert result.problems[0].startswith(problem)
