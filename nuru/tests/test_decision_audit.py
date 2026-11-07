import json
import pytest
from ulid import ULID
from core.decision_shadow import summarize_result, think_tier

NOTE_ID = str(ULID())


def test_computation_answer_is_summarized_as_an_answer():
    assert summarize_result("calculate", '{"answer": 42}', True, None) == "answered: 42"


def test_a_failed_audit_sink_cannot_disable_a_write_block_or_lose_observed_ids():
    from core.decision_shadow import DecisionShadow
    from core.providers.mcp_tools import ToolResult

    class BrokenLog:
        def record_decision(self, **kwargs):
            raise OSError("audit disk unavailable")

        def record_tool_call(self, **kwargs):
            raise OSError("audit disk unavailable")

    audit = DecisionShadow(BrokenLog(), "session")
    audit.begin("read first", "turn")
    gate = audit.before_tool("write_note", {"note": "user/private", "content": "x"})
    assert gate["verdict"] == "block" and gate["decision_id"] is None
    assert audit.failures == 1
    audit.after_tool("search", {"query": "note"}, ToolResult(json.dumps({
        "matches": [{"id": NOTE_ID, "path": "nuru/note.md", "title": "Note"}],
    })), 1.0)
    assert NOTE_ID in audit.known_ids and audit.last_search_ids == [NOTE_ID]
    assert audit.failures == 2
    assert audit.executed[-1]["ok"] is True
    assert audit.before_tool("write_note", {"note": NOTE_ID, "content": "x"}) is None


def test_audit_retry_steps_and_block_receipts_survive_reopening(tmp_path):
    from core.decision_shadow import DecisionShadow
    from core.decision_log import DecisionLog

    path = tmp_path / "previous-decisions.sqlite3"
    audit = DecisionShadow(DecisionLog(path), "session")
    audit.begin("write to my note", "turn")
    audit.model_called([], True, True)  # an empty response still used a model round
    audit.model_called(["write_note"], False, False)
    gate = audit.before_tool("write_note", {"note": "user/private", "content": "x"})
    audit.gate_action(gate, "blocked")
    audit.close()
    reopened = DecisionLog(path)
    try:
        row = reopened._conn.execute("SELECT * FROM decisions WHERE id = ?", (gate["decision_id"],)).fetchone()
        assert row["step"] == 2 and row["session_id"] == "session" and row["task_id"] == "turn"
        assert row["status"] == "code_blocked" and row["action_taken"] == "blocked"
    finally:
        reopened.close()

@pytest.mark.parametrize("tool, text, ok, error, summary", [
    ("search", json.dumps({"query": "x", "count": 5, "matches": [
        {"title": "A", "id": NOTE_ID}, {"title": "B", "id": None}, {"path": "c.md"}, {"title": "D"}]}), True, None,
     f"5 found: A ({NOTE_ID}), B, c.md and 2 more"),
    ("search", json.dumps({"query": "x", "matches": []}), True, None, "0 found"),
    ("write_note", json.dumps({"id": NOTE_ID, "path": "nuru/x.md"}), True, None, f"done: nuru/x.md {NOTE_ID}"),
    ("write_note", json.dumps({"path": "user/daily.md", "id": None, "created": True}), True, None,
     "done: user/daily.md (created)"),
    ("rename_or_move_file", json.dumps({"from": "nuru/x.md", "to": "archive/nuru/x.md"}), True, None,
     "moved nuru/x.md to archive/nuru/x.md"),
    ("write_note", "", False, "TimeoutError: the write timed out", "failed: TimeoutError: the write timed out"),
    ("read_note", "# Priya\nFirmware engineer.", True, None, "read 26 characters: # Priya Firmware engineer."),
    ("write_note", "nuru/priya-raman.md", True, None, "done: nuru/priya-raman.md"),
    ("multiply", "128.4", True, None, "128.4"),
])
def test_results_are_summarized_in_one_line(tool, text, ok, error, summary):
    assert summarize_result(tool, text, ok, error) == summary


def test_a_long_result_is_cut_to_one_short_line():
    summary = summarize_result("read_note", "word " * 500, True)
    assert len(summary) <= 160 and "\n" not in summary


@pytest.mark.parametrize("think, tier", [(False, "off"), (True, "high"), ("low", "low")])
def test_think_values_map_to_tier_names(think, tier):
    assert think_tier(think) == tier
