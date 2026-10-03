import pytest

from laya_mcp.question_sets import QuestionSet, load_question_sets

BASE = {"name": "x", "version": 1, "checkpoint": "english", "max_state_tokens": 400}


def qset(**questions):
    return QuestionSet.model_validate({**BASE, "questions": questions})


def test_the_shipped_sets_load_and_fit_their_checkpoints():
    sets = load_question_sets()
    assert set(sets) == {
        "tool_gate_start", "tool_gate_next", "assess_effort", "gate_write",
        "check_claim", "rerank", "watch_thinking", "note_type",
    }
    for s in sets.values():
        assert s.max_state_tokens < s.context_length


def test_the_file_name_must_match_the_set_name(tmp_path):
    (tmp_path / "wrong.yaml").write_text(
        "name: right\nversion: 1\ncheckpoint: english\nmax_state_tokens: 10\n"
        "questions: {q: {type: noul, instructions: x}}\n"
    )
    with pytest.raises(ValueError, match="does not match the file name"):
        load_question_sets(tmp_path)


@pytest.mark.parametrize(
    "question, message",
    [
        ({"type": "choice", "instructions": "x"}, "exactly one of criteria"),
        ({"type": "choice", "instructions": "x", "criteria": {"a": "b"}, "criteria_from": "t"}, "exactly one"),
        ({"type": "score", "instructions": "x", "criteria": {"a": "b"}}, "list of levels"),
        ({"type": "noul", "instructions": "x", "criteria": {"yes": "b"}}, "'true'/'false'"),
        ({"type": "noul", "instructions": "x", "criteria_from": "t"}, "only for choice"),
        ({"type": "choice", "instructions": "", "criteria": ["a"]}, "at least 1 character"),
    ],
)
def test_invalid_questions_are_refused(question, message):
    with pytest.raises(ValueError, match=message):
        qset(q=question)


def test_a_state_budget_must_leave_room_for_the_questions():
    with pytest.raises(ValueError, match="leaves no room"):
        QuestionSet.model_validate({**BASE, "max_state_tokens": 512, "questions": {"q": {"type": "noul", "instructions": "x"}}})


def test_only_the_multilingual_checkpoint_may_read_past_its_context():
    with pytest.raises(ValueError, match="only the multilingual"):
        QuestionSet.model_validate({**BASE, "max_len": 4096, "questions": {"q": {"type": "noul", "instructions": "x"}}})


def test_render_fills_request_time_options_in_systemone_shape():
    s = qset(tool={"type": "choice", "instructions": "Which?", "criteria_from": "tools"}, ok={"type": "noul", "instructions": "Fine?"})
    assert s.render({"tools": {"read_note": "Read a note"}}) == {
        "tool": {"type": "choice", "instructions": "Which?", "criteria": {"read_note": "Read a note"}},
        "ok": {"type": "noul", "instructions": "Fine?"},
    }


def test_render_refuses_missing_or_empty_request_time_options():
    s = qset(tool={"type": "choice", "instructions": "Which?", "criteria_from": "tools"})
    with pytest.raises(ValueError, match="needs request-time options"):
        s.render()
    with pytest.raises(ValueError, match="no options"):
        s.render({"tools": {}})
