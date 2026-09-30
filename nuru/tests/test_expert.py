import json
from types import SimpleNamespace

import pytest

from core.expert import ExpertPlanner, ExpertUnavailable, FoundNote, build_brief, notes_found
from laya_mcp.config import ExpertSettings
from laya_mcp.decision_log import DecisionLog

PRIYA_ID = "01M2X9M15NDE1FJYDMC0SPGRVA"
TOOLS = [{"type": "function", "function": {"name": "append_note", "description": "Append.",
                                           "parameters": {"properties": {"id": {"type": "string"}, "content": {"type": "string"}}}}}]

# What a session's tools returned: search results with snippets and passages, and a whole note read.
SEARCH_RESULT = json.dumps({"query": "Priya", "count": 2, "matches": [
    {"path": "nuru/priya-raman.md", "id": PRIYA_ID, "type": "person", "title": "Priya Raman",
     "snippet": "Firmware engineer, same team. Owns the bootloader and OTA path."},
    {"path": "user/daily-2026-09-17.md", "id": None, "type": "daily", "title": "Daily 2026-09-17",
     "snippet": "PR #412 (not #421. Sorry Priya.) - 16:05 Trace shows modem wakes every 47 min."},
]})
SEMANTIC_RESULT = json.dumps({"query": "who owns OTA", "matches": [
    {"path": "nuru/priya-raman.md", "id": PRIYA_ID, "score": 0.81,
     "text": "Priya Raman > Role\n\nFirmware engineer, same team. Owns the bootloader and OTA path."}]})
READ_RESULT = "# Priya Raman\n\n## Role\nFirmware engineer, same team. Owns the bootloader and OTA path.\n\n" \
              "## Confidential\nShe is interviewing elsewhere. Do not mention it."


def test_no_note_text_seen_in_the_session_reaches_the_brief():
    """The privacy guarantee: whatever the session read, only titles and IDs go to the cloud."""
    seen = [("search_notes", SEARCH_RESULT), ("semantic_search", SEMANTIC_RESULT), ("read_note", READ_RESULT)]
    notes = [n for tool, text in seen for n in notes_found(tool, text)]
    steps = [{"tool": tool, "status": "ok", "result": text[:200]} for tool, text in seen]  # results are ignored
    brief = build_brief("Add a line to Priya's note saying she prefers async on Fridays.", TOOLS, notes, steps)

    note_text = []
    for tool, text in seen:
        try:
            for match in json.loads(text)["matches"]:
                note_text += [match.get("snippet", ""), match.get("text", "")]
        except ValueError:
            note_text.append(text)
    fragments = {line.strip() for chunk in note_text for line in chunk.splitlines() if len(line.strip()) >= 12}
    fragments |= {"interviewing elsewhere", "bootloader", "modem wakes", "Firmware engineer"}
    leaked = [f for f in fragments if f.lower() in brief.lower()]
    assert leaked == [], f"note text reached the expert brief: {leaked}"
    assert "Priya Raman" in brief and PRIYA_ID in brief  # titles and IDs do


def test_only_search_results_name_notes():
    assert notes_found("read_note", READ_RESULT) == []
    assert notes_found("search_notes", SEARCH_RESULT) == [FoundNote("Priya Raman", PRIYA_ID), FoundNote("Daily 2026-09-17", None)]
    assert notes_found("search_notes", "not json") == []


def test_the_brief_has_the_request_tools_notes_and_steps():
    brief = build_brief("do it", TOOLS, [FoundNote("A", PRIYA_ID), FoundNote("A", PRIYA_ID)],
                        [{"tool": "search_notes", "status": "ok"}])
    assert "Request: do it" in brief and "append_note" in brief and '"content"' in brief
    assert brief.count("(id " + PRIYA_ID + ")") == 1
    assert "1. search_notes - ok" in brief


class FakeClient:
    def __init__(self, stop_reason="end_turn", text="1. append_note(...)", error=None):
        self.calls = []
        self._response = SimpleNamespace(stop_reason=stop_reason,
                                         content=[SimpleNamespace(type="text", text=text)] if text else [])
        self._error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


@pytest.fixture
def log(tmp_path):
    log = DecisionLog(tmp_path / "d.sqlite3")
    yield log
    log.close()


def test_a_plan_comes_back_and_the_call_is_counted(log):
    client = FakeClient()
    planner = ExpertPlanner(ExpertSettings(model="claude-opus-5", daily_cap=2), log, client)
    assert planner.plan("brief") == "1. append_note(...)"
    call = client.calls[0]
    assert call["model"] == "claude-opus-5" and call["fallbacks"] == "default"
    assert call["messages"] == [{"role": "user", "content": "brief"}]
    assert planner.calls_today() == 1


def test_the_daily_cap_stops_further_calls(log):
    planner = ExpertPlanner(ExpertSettings(model="m", daily_cap=1), log, FakeClient())
    planner.plan("brief")
    assert planner.available() is False
    with pytest.raises(ExpertUnavailable, match="budget"):
        planner.plan("brief")


@pytest.mark.parametrize("client, message", [
    (FakeClient(stop_reason="refusal", text=""), "declined"),
    (FakeClient(stop_reason="max_tokens", text=""), "no plan"),
])
def test_a_refusal_or_an_empty_plan_is_unavailable_and_still_counted(log, client, message):
    planner = ExpertPlanner(ExpertSettings(model="m", daily_cap=5), log, client)
    with pytest.raises(ExpertUnavailable, match=message):
        planner.plan("brief")
    assert planner.calls_today() == 1


def test_an_api_error_is_unavailable(log):
    import anthropic

    error = anthropic.APIConnectionError(request=SimpleNamespace(method="POST", url="https://x"))
    planner = ExpertPlanner(ExpertSettings(model="m", daily_cap=5), log, FakeClient(error=error))
    with pytest.raises(ExpertUnavailable, match="APIConnectionError"):
        planner.plan("brief")


def test_without_a_key_there_is_no_expert(log):
    planner = ExpertPlanner(ExpertSettings(model="m", daily_cap=5), log)
    assert planner.available() is False
    with pytest.raises(ExpertUnavailable, match="no API key"):
        planner.plan("brief")


def test_a_key_handed_in_makes_the_expert_available(log):
    assert ExpertPlanner(ExpertSettings(model="m", daily_cap=5), log, api_key="sk-test").available() is True


def test_the_key_comes_from_the_environment_first_then_deep_thinks_env(monkeypatch, tmp_path):
    from core import session

    monkeypatch.undo()  # the autouse fixture stubs expert_api_key; this test is about the real one
    (tmp_path / ".env.deep-think").write_text("CLAUDE_API_KEY=from-deep-think\n")
    monkeypatch.setattr(session, "ENV_DIR", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert session.expert_api_key() == "from-deep-think"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert session.expert_api_key() == "from-env"
