import json

import pytest
from ulid import ULID

from laya_mcp.vault import Vault
from laya_fakes import rows

READ = {"semantic_search": "Passages nearest in meaning", "read_note": "Read a note", "search_notes": "Search by words"}
WRITE = {"write_note": "Write to a note", "rename_or_move_file": "Move a note"}
CTX = {"session_id": "s1", "task_id": "t1", "step": 0}
ID = str(ULID())


# ---------------------------------------------------------------- tool_gate
def test_answer_needs_its_high_threshold_or_the_default_is_a_read(make_layer, log):
    layer, _ = make_layer({"mode": {"answer": 0.9, "read": 0.1}})
    result = layer.tool_gate_start("What do my notes say about Kubernetes?", CTX)
    assert (result["mode"], result["first_tool"], result["source"]) == ("read", "semantic_search", "laya")
    [row] = rows(log)
    assert row["proposed_action"] == "read" and row["decision_point"] == "tool_gate"
    assert row["session_id"] == "s1"


def test_a_confident_answer_skips_tools(make_layer):
    layer, _ = make_layer({"mode": {"answer": 0.97}})
    result = layer.tool_gate_start("What's 5.35 mA times 24 hours?")
    assert (result["mode"], result["first_tool"]) == ("answer", None)


def test_the_first_tool_is_codes_search_first_not_a_choice_laya_makes(make_layer):
    layer, predictor = make_layer({"mode": {"write": 0.9}})
    result = layer.tool_gate_start("Add a line to Priya's note")
    assert (result["mode"], result["first_tool"]) == ("write", "semantic_search")
    _, _, questions = predictor.calls[0]
    assert set(questions) == {"mode"}


def test_asking_the_user_needs_no_tool(make_layer):
    layer, _ = make_layer({"mode": {"ask_user": 0.95}})
    assert layer.tool_gate_start("Do the thing")["first_tool"] is None


def test_tool_gate_falls_back_to_a_read_when_laya_is_down(make_layer, log):
    layer, _ = make_layer(available=False)
    result = layer.tool_gate_start("anything")
    assert (result["mode"], result["first_tool"], result["source"], result["status"]) == (
        "read", "semantic_search", "fallback", "unavailable")
    assert rows(log)[0]["status"] == "unavailable"


def test_an_over_budget_request_is_rejected_not_truncated(make_layer, log):
    layer, predictor = make_layer()
    result = layer.tool_gate_start("x" * 5000)
    assert result["status"] == "over_budget" and result["source"] == "fallback"
    assert predictor.calls == []
    assert "over the 400-token budget" in rows(log)[0]["detail"]


def test_next_step_is_continue_answer_or_ask_and_defers_when_unsure(make_layer):
    steps = [{"tool": "search_notes", "status": "ok", "result": "1 found: Priya Raman"}]
    layer, _ = make_layer({"next": {"continue": 0.5, "answer": 0.5}})
    assert layer.tool_gate_next("r", steps)["next"] == "defer"
    layer, predictor = make_layer({"next": {"answer": 0.9}})
    assert layer.tool_gate_next("r", steps)["next"] == "answer"
    assert list(predictor.calls[0][2]["next"]["criteria"]) == ["continue", "answer", "ask_user"]


# ---------------------------------------------------------------- assess_effort
def test_a_tier_is_used_only_when_it_clears_its_own_threshold(make_layer):
    layer, _ = make_layer({"tier": {"off": 0.95}})
    assert layer.assess_effort("r", "arguments", [], {})["tier"] == "off"
    layer, _ = make_layer({"tier": {"off": 0.6, "high": 0.4}})
    assert layer.assess_effort("r", "arguments", [], {})["tier"] == "low"  # the config's default tier


def test_assess_effort_falls_back_to_the_default_tier(make_layer):
    layer, _ = make_layer(available=False)
    assert layer.assess_effort("r", "answer", [], {"cannot_do": 1})["tier"] == "low"


def test_step_type_is_checked(make_layer):
    layer, _ = make_layer()
    with pytest.raises(ValueError):
        layer.assess_effort("r", "planning", [], {})


# ---------------------------------------------------------------- gate_write
def test_code_blocks_before_laya_is_asked(make_layer, log):
    layer, predictor = make_layer()
    result = layer.gate_write("add a note", "write_note", {"note": str(ULID()), "content": "x"}, [ID], CTX)
    assert result["verdict"] == "block" and result["source"] == "code"
    assert predictor.calls == []
    assert rows(log)[0]["status"] == "code_blocked"


def test_proceed_needs_both_answers_to_clear(make_layer):
    args = {"note": ID, "content": "Prefers async messages on Fridays"}
    layer, _ = make_layer({"matches_request": 0.95, "verdict": {"proceed": 0.9}})
    assert layer.gate_write("add to Priya", "write_note", args, [ID])["verdict"] == "proceed"
    layer, _ = make_layer({"matches_request": 0.95, "verdict": {"proceed": 0.6, "confirm": 0.4}})
    assert layer.gate_write("add to Priya", "write_note", args, [ID])["verdict"] == "ask"
    layer, _ = make_layer({"matches_request": 0.5, "verdict": {"proceed": 0.9}})
    assert layer.gate_write("add to Priya", "write_note", args, [ID])["verdict"] == "ask"


def test_gate_write_asks_when_laya_is_down(make_layer):
    layer, _ = make_layer(available=False)
    result = layer.gate_write("r", "write_note", {"note": ID, "content": "x"}, [ID])
    assert (result["verdict"], result["source"]) == ("ask", "fallback")


# ---------------------------------------------------------------- check_claim
def test_an_invented_note_id_fails_without_asking_laya(make_layer):
    layer, predictor = make_layer()
    made_up = str(ULID())
    result = layer.check_claim(f"Done - appended to note {made_up}.", [], [ID])
    assert result["passed"] is False and result["failures"] == [f"invented note ID {made_up}"]
    assert predictor.calls == []


def test_an_id_the_harness_passed_to_a_tool_counts_as_known(make_layer):
    layer, _ = make_layer({"claim": {"made_change": 0.9}})
    log = [{"tool": "write_note", "arguments": {"note": ID}, "ok": True}]
    assert layer.check_claim(f"Appended to {ID}.", log, [])["passed"] is True


def test_laya_sees_only_the_reply_and_code_checks_it_against_the_log(make_layer):
    layer, predictor = make_layer({"claim": {"made_change": 0.9}})
    layer.check_claim("I added it.", [{"tool": "write_note", "ok": True}], [])
    _, state, questions = predictor.calls[0]
    assert state == {"reply": "I added it."} and list(questions) == ["claim"]


def test_a_claimed_change_with_no_write_behind_it_fails(make_layer):
    layer, _ = make_layer({"claim": {"made_change": 0.9}})
    result = layer.check_claim("I created a new project note for you.", [{"tool": "search_notes", "ok": True}], [])
    assert result["failures"] == ["claims a change the log does not show"]


def test_a_claimed_change_whose_write_failed_says_so(make_layer):
    layer, _ = make_layer({"claim": {"made_change": 0.9}})
    result = layer.check_claim("Done, the line is added.", [{"tool": "write_note", "ok": False}], [])
    assert result["failures"] == ["presents a failed write as successful"] and result["failed_writes"] == 1


@pytest.mark.parametrize("claim, executed", [
    ({"reports_failure": 0.9}, [{"tool": "write_note", "ok": False}]),  # an admitted failure
    ({"no_change": 0.9}, [{"tool": "read_note", "ok": True}]),  # a plain answer
    ({"made_change": 0.9}, [{"tool": "write_note", "ok": True}]),  # a change that happened
    ({"made_change": 0.4, "no_change": 0.6}, []),  # not confident it claims a change
])
def test_honest_replies_pass(make_layer, claim, executed):
    layer, _ = make_layer({"claim": claim})
    assert layer.check_claim("reply", executed, [])["passed"] is True


def test_check_claim_runs_code_only_and_marks_unverified_when_laya_is_down(make_layer):
    layer, _ = make_layer(available=False)
    result = layer.check_claim(f"Appended to {ID}.", [], [ID])
    assert (result["passed"], result["verified"], result["source"]) == (True, False, "code")


# ---------------------------------------------------------------- rerank
MATCHES = [{"path": f"n{i}.md", "id": None, "text": f"passage {i}", "score": 0.9 - i / 10} for i in range(7)]


def test_rerank_sorts_by_relevance_and_keeps_top_k(make_layer, log):
    layer, _ = make_layer(lambda state: {"relevant": int(state["passage"][-1]) / 10})
    result = layer.rerank("q", MATCHES)
    assert result["reranked"] is True
    assert [m["path"] for m in result["matches"]] == ["n6.md", "n5.md", "n4.md", "n3.md", "n2.md"]
    assert result["matches"][0]["relevance"] == 0.6
    assert len(rows(log, decision_point="rerank")) == 7


def test_rerank_keeps_the_search_order_if_any_chunk_cannot_be_scored(make_layer):
    layer, _ = make_layer()
    matches = MATCHES[:2] + [{"path": "long.md", "text": "x" * 5000}]
    result = layer.rerank("q", matches)
    assert result["reranked"] is False and result["matches"] == matches


# ---------------------------------------------------------------- classify_notes
@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    notes = {
        "nuru/People/alice.md": f"---\nid: {ID}\ntype: person\ncreated: 2026-09-10T10:00\n---\n# Alice\nWorks on firmware.",
        "nuru/topic.md": "---\ntype: topic\ncreated: 2026-09-20T10:00\n---\nFreeRTOS notes.",
        "Templates/01 Profile.md": "---\nid:\n---\n# {{title}}",
        "archive/nuru/old.md": "old",
        ".obsidian/workspace.md": "config",
    }
    for rel, text in notes.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return Vault(root)


def test_classify_returns_labels_and_never_content(make_layer, vault, log):
    layer, predictor = make_layer({"note_type": {"person": 0.9}}, vault=vault)
    result = layer.classify_notes("note_type", folder="nuru")
    assert result == {"count": 2, "labelled": [{"id": "nuru/topic.md", "label": "person", "p": 0.9},
                                               {"id": ID, "label": "person", "p": 0.9}], "uncertain": 0}
    assert "firmware" not in json.dumps(result)
    # Frontmatter is stripped: it names the type.
    assert all("type:" not in state["note"] for _, state, _ in predictor.calls)
    assert rows(log)[0]["mode"] == "tool"


def test_the_note_types_are_the_vaults_own_eleven(make_layer, vault):
    layer, predictor = make_layer(vault=vault)
    layer.classify_notes("note_type", ids=[ID])
    _, _, questions = predictor.calls[0]
    assert len(questions["note_type"]["criteria"]) == 11 and "correction" in questions["note_type"]["criteria"]


def test_uncertain_notes_are_counted_not_listed(make_layer, vault):
    layer, _ = make_layer({"note_type": {"person": 0.4, "topic": 0.35}}, vault=vault)
    assert layer.classify_notes("note_type", folder="nuru") == {"count": 2, "labelled": [], "uncertain": 2}


def test_an_unchanged_note_is_not_classified_twice(make_layer, vault):
    layer, predictor = make_layer({"note_type": {"topic": 0.9}}, vault=vault)
    layer.classify_notes("note_type", folder="nuru")
    layer.classify_notes("note_type", folder="nuru")
    assert len(predictor.calls) == 2


def test_subject_takes_the_callers_labels(make_layer, vault):
    layer, predictor = make_layer({"subject": {"firmware": 0.8}}, vault=vault)
    result = layer.classify_notes("subject", ids=[ID], labels=["firmware", "personal"])
    assert result["labelled"] == [{"id": ID, "label": "firmware", "p": 0.8}]
    assert set(predictor.calls[0][2]["subject"]["criteria"]) == {"firmware", "personal"}
    with pytest.raises(ValueError, match="2 to 10"):
        layer.classify_notes("subject", ids=[ID], labels=["only one"])


def test_selectors(make_layer, vault):
    layer, _ = make_layer(vault=vault)
    assert layer.classify_notes("note_type", date_from="2026-09-15")["count"] == 1
    missing = str(ULID())
    assert layer.classify_notes("note_type", ids=[ID, missing])["not_found"] == [missing]
    assert layer.classify_notes("note_type", folder="Templates")["count"] == 0
    with pytest.raises(ValueError, match="exactly one selector"):
        layer.classify_notes("note_type", ids=[ID], folder="nuru")
    with pytest.raises(ValueError, match="outside the vault"):
        layer.classify_notes("note_type", folder="../")


def test_classify_reports_when_laya_is_down(make_layer, vault):
    layer, _ = make_layer(available=False, vault=vault)
    with pytest.raises(RuntimeError, match="not available"):
        layer.classify_notes("note_type", folder="nuru")


# ---------------------------------------------------------------- startup validation
def test_thresholds_must_name_real_question_sets_and_questions(log):
    from laya_mcp.config import load_config
    from laya_mcp.decisions import DecisionLayer
    from laya_mcp.question_sets import load_question_sets
    from laya_fakes import FakePredictor

    config = load_config()
    raw = config.model_dump(mode="json")
    raw["thresholds"]["gate_write"]["telepathy"] = raw["thresholds"]["gate_write"]["verdict"]
    with pytest.raises(ValueError, match="unknown questions"):
        DecisionLayer(type(config).model_validate(raw), load_question_sets(), FakePredictor(), log)


# ---------------------------------------------------------------- watch_thinking
def test_watch_thinking_signals_only_past_its_threshold(make_layer, log):
    layer, predictor = make_layer({"state": {"repeating": 0.9}})
    result = layer.watch_thinking("r", [], "so the answer is... so the answer is...", CTX)
    assert result["signal"] == "repeating" and result["source"] == "laya"
    assert predictor.calls[0][1] == {"request": "r", "steps": [], "thinking": "so the answer is... so the answer is..."}
    layer, _ = make_layer({"state": {"ready": 0.6, "progressing": 0.4}})
    assert layer.watch_thinking("r", [], "x")["signal"] == "progressing"


def test_watch_thinking_lets_the_model_go_on_when_laya_is_down(make_layer):
    layer, _ = make_layer(available=False)
    assert layer.watch_thinking("r", [], "x")["signal"] == "progressing"
