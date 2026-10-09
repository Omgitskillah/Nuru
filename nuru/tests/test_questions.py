import json
from pathlib import Path

import pytest

from core import questions
from core.questions import MULTI, NO, SINGLE, YES, YES_NO, Option, Question

EVALS = Path(__file__).resolve().parents[1] / "evals"


def test_a_yes_no_question_offers_yes_and_no():
    question = questions.from_tool({"question": "Use the revised capacity?", "kind": "yes_no", "recommended": "Yes"})
    assert [o.label for o in question.choices] == [YES, NO]
    assert question.recommended == Option(YES, recommended=True)


def test_options_keep_their_order_description_and_recommendation():
    question = questions.from_tool({"question": "Which rate?", "kind": "single", "options": [
        {"label": "4/h", "description": "the revised note", "recommended": True}, "6/h"]})
    assert question.options == (Option("4/h", "the revised note", True), Option("6/h"))
    assert question.allow_other


def test_arguments_may_arrive_as_a_json_string():
    assert questions.from_tool(json.dumps({"question": "Go?", "kind": "yes_no"})).text == "Go?"


@pytest.mark.parametrize("arguments, problem", [
    ({"kind": "yes_no"}, "question is empty"),
    ({"question": "Go?", "kind": "maybe"}, "kind must be"),
    ({"question": "Go?", "kind": "yes_no", "recommended": "Perhaps"}, "Yes or No"),
    ({"question": "Which?", "kind": "single", "options": ["only one"]}, "2-6 options"),
    ({"question": "Which?", "kind": "multi", "options": list("abcdefg")}, "2-6 options"),
    ({"question": "Which?", "kind": "single", "options": ["A", "a"]}, "must differ"),
    ({"question": "Which?", "kind": "single", "options": [{"label": ""}, "B"]}, "needs a label"),
    ({"question": "Which?", "kind": "single", "options": [{"label": "A", "recommended": True},
                                                           {"label": "B", "recommended": True}]}, "at most one"),
    ("not json", "aren't JSON"),
])
def test_arguments_that_make_no_question_are_refused_in_words_the_model_can_act_on(arguments, problem):
    with pytest.raises(ValueError, match=problem):
        questions.from_tool(arguments)


def test_as_text_lists_the_options_and_marks_the_recommendation():
    question = Question("Which rate?", SINGLE, (Option("4/h", "the revised note", True), Option("6/h")))
    assert questions.as_text(question) == "Which rate?\n\n- 4/h (recommended): the revised note\n- 6/h"
    assert questions.as_text(questions.yes_no("Go?")) == "Go?"  # nothing to add to a plain yes/no


def test_answer_text_joins_several_choices_and_says_when_none_was_picked():
    assert questions.answer_text(["A", "C"]) == "A; C"
    assert questions.answer_text([]) == "None of these."


def test_only_a_yes_is_yes():
    assert questions.is_yes([YES])
    assert not any(questions.is_yes(a) for a in ([NO], None, [], [YES, NO]))


def test_a_write_question_names_the_note_and_shows_the_arguments():
    question = questions.write("write_note", {"note": "projects/a.md", "content": "x"})
    assert question.text == "Nuru wants to run write_note on projects/a.md. Allow it?"
    assert question.kind == YES_NO and question.recommended is None
    assert '"content": "x"' in question.details


def test_questions_code_raises_wait_ten_seconds_and_those_the_model_asks_do_not():
    raised = [questions.write("write_note", {}), questions.against_no_save("write_note", {}, "no edits"),
              questions.shared_write("write_note", {}, "shared/a.md")]
    assert [q.timeout for q in raised] == [10, 10, 10]
    assert questions.from_tool({"question": "Go?", "kind": "yes_no"}).timeout is None


def test_a_write_into_shared_names_the_path_and_who_can_read_it():
    question = questions.shared_write("rename_or_move_file", {"destination_path": "shared/a.md"}, "shared/a.md")
    assert question.text == "Nuru wants to run rename_or_move_file on shared/a.md, where others can read it. Allow it?"
    assert question.recommended is None


def test_a_save_the_message_ruled_out_is_asked_about_with_no_recommended():
    question = questions.against_no_save("write_note", {"content": "x"}, "Do not save it")
    assert question.text.startswith('Your message said "Do not save it", but Nuru wants to run write_note on a new note.')
    assert question.recommended.label == NO


@pytest.mark.parametrize("text, said", [
    ("Write a three-bullet summary here. Do not save it.", "Do not save it"),
    ("Give a public-safe profile; do not save yet.", "do not save yet"),
    ("Use the documented assumptions; no edits.", "no edits"),
    ("Check the links without changing files.", "without changing files"),
    ("Don't write anything to my notes", "Don't write anything to my notes"),
    ("Save the summary to my notes.", None),
    ("Do note the deadline and save it to my daily note.", None),
    ("What changes did I make yesterday?", None),
])
def test_no_save_phrase_finds_the_words_that_rule_out_writing(text, said):
    assert questions.no_save_phrase(text) == said


def eval_prompts():
    legacy = json.loads((EVALS / "decision_layer" / "cases.json").read_text())
    for case in legacy.values():
        yield from ([case["prompt"]] if "prompt" in case else [])
        yield from (turn["prompt"] for turn in case.get("turns", []))
    for case in json.loads((EVALS / "multi_turn" / "cases.json").read_text()).values():
        yield from (turn["prompt"] for turn in case["turns"])
    for case in json.loads((EVALS / "multi_turn" / "failure_pairs" / "cases.json").read_text())["cases"]:
        yield from (turn["prompt"] for turn in case["multi_turn"])


WRITE_REQUEST = ("save", "add ", "append", "create", "write a ", "update", "rename", "move", "record", "log ")


def test_no_eval_prompt_that_matches_also_asks_for_a_write():
    # The check withholds writes, so a match on a prompt that wants one would cost that test its write.
    # Each match must be a turn that only forbids writing: none of them may also ask for one.
    matched = [p for p in eval_prompts() if questions.no_save_phrase(p)]
    assert len(matched) >= 40
    for prompt in matched:
        rest = prompt.lower().replace(questions.no_save_phrase(prompt).lower(), "")
        asks = [w for w in WRITE_REQUEST if w in rest]
        assert not asks or any(prompt.startswith(start) for start in KNOWN_READ_ONLY), (prompt, asks)


# Matches whose other words only sound like a write request, each read and judged by hand (2026-10-09):
# "write ... here" is the reply, "recorded" and "status updates" describe notes, and the moved date is
# the conversation's ("Do not edit or schedule anything").
KNOWN_READ_ONLY = (
    "Write a three-bullet manager summary here",
    "From Berlin Admin Chaos Notes, give the recorded",
    "Move one of those dates back a week",
    "Locate every note that records the SARA-R510S",
    "Audit the project notes, their status updates",
    "Gather current project status and the relevant corrections for a short manager update",
    "Find and read my internet-contract note. Identify the recorded",
    "Using the current date supplied for this run, calculate calendar days to the recorded",
)
