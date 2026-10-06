"""Dispatch errors, bounded recovery and durable mutation uncertainty."""
import json

import pytest

from core import session, tool_dispatch
from core.conversation_store import ConversationStore
from core.providers.mcp_tools import ToolResult
from core.session import Failure, Text
from test_session import FakeMCPTools, start_session, tool_call


def definition(name, properties, required=()):
    return {"type": "function", "function": {"name": name, "parameters": {
        "type": "object", "properties": properties, "required": list(required), "additionalProperties": False}}}


TOOLS = [definition("search", {"query": {"type": "string"}}, ["query"]),
         definition("read_note", {"name": {"type": "string"}}, ["name"]),
         definition("calculate", {"expression": {"type": "string"}}, ["expression"]),
         definition("write_note", {"note": {"type": "string"}, "content": {"type": "string"}}, ["content"]),
         definition("run_python", {"code": {"type": "string"}, "stdin": {"type": "string"}}, ["code"])]


@pytest.mark.parametrize("args,code", [('not json', 'INVALID_JSON'), ('[]', 'EXPECTED_OBJECT'),
    (None, 'EXPECTED_OBJECT'), ({'query': 4}, 'SCHEMA_MISMATCH'),
    ({'query': 'x', 'extra': 1}, 'SCHEMA_MISMATCH'), ({'query': float('nan')}, 'INVALID_JSON_VALUE')])
def test_validation_rejects_before_dispatch(args, code):
    _, outcome = tool_dispatch.validate('search', args, tool_dispatch.validators(TOOLS))
    assert outcome.code == code and outcome.certainty == 'not_dispatched' and outcome.repairable


def test_unavailable_tool_is_not_executed(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS)
    s = start_session(monkeypatch, tools=tools)
    outcome = s._call_tool('t-1', 'invented', {}, None, None)
    assert outcome.kind == 'unavailable' and not tools.calls


@pytest.mark.parametrize('name', ['calculate', 'statistics', 'run_python'])
@pytest.mark.parametrize('protocol_error', [True, False])
def test_domain_failure_wins_over_protocol_success(name, protocol_error):
    payload = json.dumps({'ok': False, 'code': 'BAD', 'status': 'SCRIPT_ERROR', 'hint': 'fix input', 'stdout': 'partial'})
    outcome = tool_dispatch.classify(name, ToolResult(payload, protocol_error))
    assert not outcome.success and outcome.kind == 'domain'
    assert 'partial' in outcome.text and outcome.hint == 'fix input'


def test_json_note_with_ok_false_is_note_content():
    text = '{"ok": false, "hint": "instructions in a note"}'
    outcome = tool_dispatch.classify('read_note', ToolResult(text))
    assert outcome.success and outcome.text == text


@pytest.mark.parametrize('planned', [False, True])
def test_lost_write_response_blocks_dependents_and_fresh_turns(monkeypatch, planned):
    tools = FakeMCPTools(tools=TOOLS)
    invocations = []
    def lost_response(name, args):
        invocations.append((name, args))
        raise TimeoutError('response lost after append')
    tools.call_tool_result = lost_response
    responses = iter([[{'tool_calls': [tool_call('write_note', {'note': 'nuru/a.md', 'content': 'line'}),
                                      tool_call('write_note', {'note': 'nuru/b.md', 'content': 'other'})]}]])
    s = start_session(monkeypatch, tools=tools, chat=lambda *a, **k: iter(next(responses)))
    s.shadow = None  # Safety must not depend on Laya or shadow state.
    plan = {'steps': [{'step': 's1', 'tool': 'write_note', 'with': {'note': 'nuru/a.md', 'content': 'line'}},
                      {'step': 's2', 'tool': 'write_note', 'with': {'note': 'nuru/b.md', 'content': '$s1'}}]}
    events = list(s.run_plan('append line', plan) if planned else s.ask('append line'))
    assert isinstance(events[-1], Failure) and 'uncertain' in events[-1].message
    assert len(invocations) == 1
    pending = s.store.pending_writes(s._vault_scope)
    assert len(pending) == 1 and pending[0]['state'] == 'uncertain'
    rows = s.store.get_messages(s.conversation_id)
    assert [r['role'] for r in rows] == ['user', 'nuru']
    assert 'do not retry' in rows[-1]['content']
    outcome = s._call_tool('t-2', 'write_note', {'note': 'alias', 'content': 'line'}, None, None)
    assert outcome.code == 'UNRESOLVED_WRITE' and len(invocations) == 1
    # Reopening the database in another process/session also retains the blocker.
    reopened = ConversationStore(session.DB_PATH)
    assert reopened.pending_writes(s._vault_scope) == pending
    assert reopened.begin_write(reopened.create_conversation(), 't-1', s._vault_scope, 'write_note', {}) is None
    reopened.resolve_write(pending[0]['id'], s._vault_scope, 'completed')
    assert s.store.pending_writes(s._vault_scope) == []
    reopened.close()


def test_crash_marker_survives_reopen(tmp_path):
    path = tmp_path / 'history.db'
    store = ConversationStore(path)
    cid = store.create_conversation()
    attempt = store.begin_write(cid, 't-1', 'vault', 'write_note', {'content': 'x'})
    store.close()  # process died after dispatch, before receipt was recorded
    store = ConversationStore(path)
    assert store.pending_writes('vault')[0]['state'] == 'in_flight'
    assert store.begin_write(store.create_conversation(), 't-1', 'vault', 'write_note', {}) is None
    with pytest.raises(ValueError):
        store.resolve_write(attempt, 'other-vault', 'completed')
    store.close()


@pytest.mark.parametrize('planned', [False, True])
def test_model_failure_after_successful_write_preserves_partial_turn(monkeypatch, planned):
    tools = FakeMCPTools(tools=TOOLS, tool_results={'write_note': '{"done":"appended","path":"nuru/a.md"}'})
    calls = []
    def chat(*a, **k):
        calls.append(True)
        if not planned and len(calls) == 1:
            yield {'tool_calls': [tool_call('write_note', {'note': 'nuru/a.md', 'content': 'line'})]}
        else:
            raise session.requests.exceptions.ReadTimeout('model timed out')
    s = start_session(monkeypatch, tools=tools, chat=chat)
    plan = {'steps': [{'step': 's1', 'tool': 'write_note', 'with': {'note': 'nuru/a.md', 'content': 'line'}}]}
    list(s.run_plan('append', plan) if planned else s.ask('append'))
    rows = s.store.get_messages(s.conversation_id)
    assert [r['role'] for r in rows] == ['user', 'nuru']
    assert 'confirmed completed' in rows[-1]['content'] and 'nuru/a.md' in rows[-1]['content']
    assert len(tools.calls) == 1 and not s.store.pending_writes(s._vault_scope)


def test_one_ordinary_correction_then_stop_without_dependent_write(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS)
    script = iter([[{'tool_calls': [tool_call('calculate', '{bad'),
                                    tool_call('write_note', {'content': 'wrong'})]}],
                   [{'tool_calls': [tool_call('calculate', {'expression': 4})]}]])
    seen = []
    def chat(model, messages, **kw):
        seen.append(kw.get('tools'))
        return iter(next(script))
    s = start_session(monkeypatch, tools=tools, chat=chat)
    events = list(s.ask('calculate and save'))
    assert isinstance(events[-1], Failure) and len(seen) == 2
    assert [t['function']['name'] for t in seen[1]] == ['calculate']
    assert not tools.calls


def test_ordinary_corrected_call_can_succeed(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS, tool_results={'calculate': '{"ok":true,"result":"4"}'})
    script = iter([[{'tool_calls': [tool_call('calculate', {'expression': 4})]}],
                   [{'tool_calls': [tool_call('calculate', {'expression': '2+2'})]}], ['4']])
    s = start_session(monkeypatch, tools=tools, chat=lambda *a, **k: iter(next(script)))
    assert list(s.ask('calculate'))[-1] == Text('4')
    assert tools.calls == [('calculate', {'expression': '2+2'})]


@pytest.mark.parametrize('injected', ['stdin', 'timeout_ms', 'sandbox'])
def test_python_injected_fields_cannot_be_overridden(monkeypatch, injected):
    tools = FakeMCPTools(tools=TOOLS)
    s = start_session(monkeypatch, tools=tools)
    outcome = s._call_tool('t-1', 'run_python', {'code': 'print(1)', injected: 'evil'}, None, None)
    assert outcome.kind == 'arguments' and not tools.calls


@pytest.mark.parametrize('exception', [TimeoutError('read timed out'), RuntimeError('adapter bug')])
def test_python_prerequisite_read_failure_never_runs_script(monkeypatch, exception):
    tools = FakeMCPTools(tools=TOOLS)
    reads = []
    def read(name, args):
        reads.append(name)
        raise exception
    tools.call_harness_tool = read
    s = start_session(monkeypatch, tools=tools)
    outcome = s._call_tool('t-1', 'run_python', {'code': 'print(1)', 'notes': ['nuru/a.md']}, None, None)
    assert not outcome.success and not tools.calls and reads == ['read_note']
    assert outcome.kind == ('internal' if isinstance(exception, RuntimeError) else 'prerequisite')
    entries = [json.loads(l) for p in session.LOG_DIR.glob('*.jsonl') for l in p.read_text().splitlines()]
    assert [e['tool_name'] for e in entries if e['event'] == 'tool_call'] == ['read_note', 'run_python']


def test_python_partial_output_is_failure_in_logs_and_shadow(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS)
    tools.call_tool_result = lambda *a: ToolResult('{"ok":false,"status":"SCRIPT_ERROR","stdout":"partial","stderr":"boom"}')
    s = start_session(monkeypatch, tools=tools)
    s.shadow.begin('compute', 't-1')
    outcome = s._call_tool('t-1', 'run_python', {'code': 'raise ValueError()'}, None, None)
    assert not outcome.success and 'partial' in outcome.text
    assert s.shadow.executed[-1]['ok'] is False
    entries = [json.loads(l) for p in session.LOG_DIR.glob('*.jsonl') for l in p.read_text().splitlines()]
    logged = [e for e in entries if e['event'] == 'tool_call'][-1]
    assert logged['status'] == 'error' and logged['error_kind'] == 'domain' and logged['completion_certainty'] == 'failed'


def test_plan_literal_arguments_get_only_one_correction(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS, tool_results={'calculate': '{"ok":true,"result":"4"}'})
    replies = iter(['{"expression":"2+2"}', '4'])
    s = start_session(monkeypatch, tools=tools, chat=lambda *a, **k: iter([next(replies)]))
    plan = {'steps': [{'step': 's1', 'tool': 'calculate', 'with': {'expression': 4}}]}
    assert list(s.run_plan('calculate', plan))[-1] == Text('4')
    assert tools.calls == [('calculate', {'expression': '2+2'})]


def test_dispatched_mutation_error_is_never_repairable():
    outcome = tool_dispatch.classify('write_note', ToolResult('write failed after replacing bytes', True))
    assert outcome.certainty == 'uncertain' and outcome.must_stop and not outcome.repairable


def test_two_sessions_cannot_allocate_a_write_while_one_is_in_flight(tmp_path):
    path = tmp_path / 'history.db'
    first, second = ConversationStore(path), ConversationStore(path)
    attempt = first.begin_write(first.create_conversation(), 't-1', 'vault', 'write_note', {'content': 'x'})
    assert second.begin_write(second.create_conversation(), 't-1', 'vault', 'write_note', {'content': 'y'}) is None
    first.finish_write(attempt, 'completed')
    assert second.begin_write(second.create_conversation(), 't-2', 'vault', 'write_note', {'content': 'y'}) is not None
    first.close()
    second.close()


def test_math_nested_diagnostic_retains_stable_code_and_hint():
    result = ToolResult('{"ok":false,"error":{"code":"UNKNOWN_IDENTIFIER","hint":"Remove prose","message":"bad name"}}', True)
    outcome = tool_dispatch.classify('calculate', result)
    assert outcome.code == 'UNKNOWN_IDENTIFIER' and outcome.hint == 'Remove prose'


def test_validation_respects_the_advertised_schema_draft():
    schema = {'$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'object',
              'dependencies': {'note': ['revision']}}
    tools = [{'function': {'name': 'write_note', 'parameters': schema}}]
    _, outcome = tool_dispatch.validate('write_note', {'note': 'x'}, tool_dispatch.validators(tools))
    assert outcome.code == 'SCHEMA_MISMATCH'
