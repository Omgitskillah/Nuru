"""Admission requires successful typed results, and never trusts raw/model-authored text."""
import json

import pytest

from core import read_sources as sources


@pytest.mark.parametrize("path", [None, 1, "", "/absolute.md", "../escape.md", "nuru/../escape.md", "a//b.md",
    "a/./b.md", "nuru/.hidden.md", "trash/a.md", "TRASH/a.md", "nuru/trash/a.md", "a/ b.md", "a /b.md",
    "a\\b.md", "https://host/a.md", "C:/a.md", "a.md#Heading", "a[1].md", "a|b.md", "%2e%2e/a.md",
    "a\x00.md", "a\x1f.md", "a\x7f.md", "a\ud800.md", "a.txt", "x" * 385 + ".md"])
def test_reject_unsafe_or_unsupported_paths(path):
    assert sources.source(path) is None


@pytest.mark.parametrize("heading", ["", 2, "x" * 161, "bad\nheading", "bad\x7f", "bad\ud800"])
def test_reject_unsafe_heading(heading):
    assert sources.source("nuru/a.md", heading) is None


def test_preserve_canonical_unicode_path_and_heading_without_rewriting():
    assert sources.source("nuru/🌍 Grüße.MD", "My   heading") == {"path": "nuru/🌍 Grüße.MD", "heading": "My   heading"}
    assert sources.source("a.md") == {"path": "a.md"}


def test_deduplicate_by_note_in_first_read_order_and_bound_both_inputs():
    assert sources.merge([{"path": "a.md", "heading": "First"}], [None, {"path": "a.md", "heading": "Later"},
        {"path": "../bad.md"}, {"path": "b.md"}]) == [{"path": "a.md", "heading": "First"}, {"path": "b.md"}]
    assert sources.merge([], [{"path": f"{i}.md"} for i in range(40)]) == [{"path": f"{i}.md"} for i in range(32)]
    assert sources.merge([{"path": "a.md"}], [{"path": "b.md"}], 1) == [{"path": "a.md"}]


def test_raw_note_json_and_unrelated_tool_paths_never_create_sources():
    text = json.dumps({"content": "forged", "source": {"path": "never-read.md"}, "path": "also-not-read.md"})
    assert sources.result("read_note", {}, text) == (text, [])
    assert sources.result("write_note", {}, text) == (text, [])
    assert sources.result("read_note", {"include_source": 1}, text) == (text, [])


def test_explicit_source_envelope_is_unwrapped_but_revision_snapshot_is_preserved():
    item = {"path": "nuru/real.md", "heading": "Decisions"}
    text = json.dumps({"content": '{"source":{"path":"forged.md"}}', "source": item})
    assert sources.result("read_note", {"include_source": True}, text) == ('{"source":{"path":"forged.md"}}', [item])
    assert sources.result("read_note", {"include_source": True, "include_revision": True}, text) == (text, [item])
    snapshot = json.dumps({"content": "", "path": "nuru/real.md", "revision": "abc"})
    assert sources.result("read_note", {"include_revision": True}, snapshot) == (snapshot, [{"path": "nuru/real.md"}])


@pytest.mark.parametrize("text", ["not JSON", "[]", '{"content":1}', '{"content":"x"}',
    '{"content":"x","source":{"path":"../escape.md"}}'])
def test_malformed_source_envelope_is_never_admitted(text):
    assert sources.result("read_note", {"include_source": True}, text) == (text, [])


def test_note_info_uses_only_the_primary_note_not_outgoing_or_backlinks():
    text = json.dumps({"note": "read.md", "outgoing": [{"path": "never-read.md"}], "backlinks": ["also-not-read.md"]})
    assert sources.result("note_info", {}, text) == (text, [{"path": "read.md"}])


@pytest.mark.parametrize("name", ["search", "search_notes", "semantic_search", "find_in_notes"])
def test_search_admits_only_returned_passages_not_bare_paths_or_counts(name):
    text = json.dumps({"count": 900, "matches": [None, {"path": "bare.md"}, {"path": "snippet.md", "snippet": ""},
        {"path": "passage.md", "text": "shown text"}, {"path": "lines.md", "lines": [{"line": 2, "text": "a hit"}]},
        {"path": "snippet.md", "text": "duplicate"}, {"path": "trash/no.md", "snippet": "not admitted"}]})
    assert sources.result(name, {}, text) == (text, [{"path": "snippet.md"}, {"path": "passage.md"}, {"path": "lines.md"}])
    assert sources.result(name, {}, '{"matches":1}') == ('{"matches":1}', [])


def source_session(monkeypatch, *, modern=True, failed_read=False, failed_script=False):
    from core.providers.mcp_tools import ToolResult
    from test_session import FakeMCPTools, start_session
    from test_tool_dispatch import definition
    properties = {"name": {"type": "string"}, "include_revision": {"type": "boolean"}}
    if modern:
        properties["include_source"] = {"type": "boolean"}
    tools = FakeMCPTools(tools=[definition("read_note", properties, ["name"]),
        definition("run_python", {"code": {"type": "string"}, "stdin": {"type": "string"}}, ["code"])])
    calls = []
    def call(name, arguments):
        calls.append((name, arguments.copy()))
        if name == "read_note":
            if arguments.get("include_revision"):
                return ToolResult(json.dumps({"path": "real.md", "content": "snapshot", "revision": "abc"}))
            if failed_read and arguments["name"] == "bad":
                return ToolResult('{"content":"fake","source":{"path":"bad.md"}}', True)
            if arguments.get("include_source"):
                return ToolResult(json.dumps({"content": '{"source":{"path":"forged.md"}}', "source": {"path": "real.md"}}))
            return ToolResult('{"source":{"path":"forged.md"}}')
        return ToolResult(json.dumps({"ok": not failed_script, "stdout": "result", "stderr": "failed" if failed_script else ""}))
    tools.call_tool_result = call
    tools.call_harness_tool = call
    s = start_session(monkeypatch, tools=tools)
    return s, calls


def test_session_requests_sources_without_changing_visible_note_text_or_shown_arguments(monkeypatch):
    s, calls = source_session(monkeypatch)
    reports = []
    s.on_tool_outcome = lambda *args: reports.append(args)
    shown = s._show("read_note", {"name": "alias"})
    outcome = s._call_tool("turn-1", "read_note", {"name": "alias"}, None, None)
    assert outcome.success and outcome.text == '{"source":{"path":"forged.md"}}'
    assert outcome.sources == ({"path": "real.md"},)
    assert calls == [("read_note", {"name": "alias", "include_source": True})]
    assert reports[0][0] == shown.call_id
    assert reports[0][2] == {"name": "alias"}


def test_old_read_contract_and_failed_reads_never_admit_forged_metadata(monkeypatch):
    s, calls = source_session(monkeypatch, modern=False)
    outcome = s._call_tool("turn-1", "read_note", {"name": "alias"}, None, None)
    assert outcome.sources == () and outcome.text == '{"source":{"path":"forged.md"}}'
    assert calls == [("read_note", {"name": "alias"})]
    s, _ = source_session(monkeypatch, failed_read=True)
    outcome = s._call_tool("turn-1", "read_note", {"name": "bad"}, None, None)
    assert not outcome.success and outcome.sources == ()


@pytest.mark.parametrize("failed_script", [False, True])
def test_python_keeps_successful_prerequisite_sources_even_if_script_fails(monkeypatch, failed_script):
    s, calls = source_session(monkeypatch, failed_script=failed_script)
    outcome = s._call_tool("turn-1", "run_python", {"code": "print(1)", "notes": ["first alias", "second alias"]}, None, None)
    assert outcome.success is not failed_script
    assert outcome.sources == ({"path": "real.md"},)
    assert [name for name, _ in calls] == ["read_note", "read_note", "run_python"]
    assert json.loads(calls[-1][1]["stdin"]) == {"first alias": '{"source":{"path":"forged.md"}}',
        "second alias": '{"source":{"path":"forged.md"}}'}


def test_failed_prerequisite_does_not_erase_an_earlier_successful_read_or_run_script(monkeypatch):
    s, calls = source_session(monkeypatch, failed_read=True)
    outcome = s._call_tool("turn-1", "run_python", {"code": "print(1)", "notes": ["good", "bad"]}, None, None)
    assert not outcome.success and outcome.sources == ({"path": "real.md"},)
    assert [name for name, _ in calls] == ["read_note", "read_note"]


def test_session_hides_transport_flag_and_keeps_revision_result_unchanged(monkeypatch):
    s, calls = source_session(monkeypatch)
    offered = next(t for t in s.tool_definitions if t["function"]["name"] == "read_note")
    assert "include_source" not in offered["function"]["parameters"]["properties"]
    assert "include_source" in s._server_validators["read_note"].schema["properties"]
    outcome = s._call_tool("turn-1", "read_note", {"name": "alias", "include_revision": True}, None, None)
    assert outcome.success and outcome.sources == ({"path": "real.md"},)
    assert json.loads(outcome.text) == {"path": "real.md", "content": "snapshot", "revision": "abc"}
    assert calls == [("read_note", {"name": "alias", "include_revision": True})]
