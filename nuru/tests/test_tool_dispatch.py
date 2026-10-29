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


def test_lost_write_response_blocks_dependents_and_fresh_turns(monkeypatch):
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
    events = list(s.ask('append line'))
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


def test_model_failure_after_successful_write_preserves_partial_turn(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS, tool_results={'write_note': '{"done":"appended","path":"nuru/a.md"}'})
    calls = []
    def chat(*a, **k):
        calls.append(True)
        if len(calls) == 1:
            yield {'tool_calls': [tool_call('write_note', {'note': 'nuru/a.md', 'content': 'line'})]}
        else:
            raise session.requests.exceptions.ReadTimeout('model timed out')
    s = start_session(monkeypatch, tools=tools, chat=chat)
    list(s.ask('append'))
    rows = s.store.get_messages(s.conversation_id)
    assert [r['role'] for r in rows] == ['user', 'nuru']
    assert 'confirmed completed' in rows[-1]['content'] and 'nuru/a.md' in rows[-1]['content']
    assert len(tools.calls) == 1 and not s.store.pending_writes(s._vault_scope)


def test_two_ordinary_corrections_then_answer_without_dependent_write(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS)
    script = iter([[{'tool_calls': [tool_call('calculate', '{bad'),
                                    tool_call('write_note', {'content': 'wrong'})]}],
                   [{'tool_calls': [tool_call('calculate', {'expression': 4})]}],
                   [{'tool_calls': [tool_call('calculate', {'expression': 5})]}],
                   ['Could not compute; no note written.']])
    seen = []
    def chat(model, messages, **kw):
        seen.append(kw.get('tools'))
        return iter(next(script))
    s = start_session(monkeypatch, tools=tools, chat=chat)
    events = list(s.ask('calculate and save'))
    assert events[-1] == Text('Could not compute; no note written.') and len(seen) == 4
    assert seen == [s.tool_definitions] * 4  # unchanged on the final answer, so the cached prompt holds
    assert 'write_note' in {t['function']['name'] for t in seen[1]}
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


def rejected(message='Replacement requires expected_revision'):
    return ToolResult(json.dumps({'code': 'REJECTED_BEFORE_WRITE', 'completion_certainty': 'not_completed',
                                  'message': message}), True)


@pytest.mark.parametrize('name', ['write_note', 'rename_or_move_file'])
def test_explicit_prewrite_rejection_is_repairable_but_exception_prefix_is_not(name):
    outcome = tool_dispatch.classify(name, rejected())
    assert outcome.certainty == 'not_completed' and outcome.repairable and not outcome.must_stop
    for text in ['ValueError: Move recovery refused: destination changed', 'PermissionError: denied',
                 '{"code":"REJECTED_BEFORE_WRITE"}']:
        outcome = tool_dispatch.classify(name, ToolResult(text, True))
        assert outcome.certainty == 'uncertain' and outcome.must_stop and not outcome.repairable


def test_ordinary_second_correction_can_succeed(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS)
    script = iter([[{'tool_calls': [tool_call('calculate', {'expression': 1})]}],
                   [{'tool_calls': [tool_call('calculate', {'expression': 2})]}],
                   [{'tool_calls': [tool_call('calculate', {'expression': '2+2'})]}], ['4']])
    s = start_session(monkeypatch, tools=tools, chat=lambda *a, **k: iter(next(script)))
    assert list(s.ask('compute'))[-1] == Text('4')
    assert tools.calls == [('calculate', {'expression': '2+2'})]


def test_cross_tool_recovery_does_not_clear_failed_evidence_until_repaired(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS)
    calls = []
    def dispatch(name, args):
        calls.append((name, args))
        return ToolResult('FileNotFoundError: wrong name', True) if name == 'read_note' and args['name'] == 'wrong' else ToolResult('found')
    tools.call_tool_result = dispatch
    script = iter([[{'tool_calls': [tool_call('read_note', {'name': 'wrong'})]}],
                   [{'tool_calls': [tool_call('search', {'query': 'find correct name'})]}],
                   [{'tool_calls': [tool_call('read_note', {'name': 'right'})]}],
                   [{'tool_calls': [tool_call('write_note', {'content': 'verified'})]}], ['saved']])
    seen = []
    def chat(*a, **kw):
        seen.append(kw.get('tools'))
        return iter(next(script))
    s = start_session(monkeypatch, tools=tools, chat=chat)
    assert list(s.ask('read and save'))[-1] == Text('saved')
    assert [n for n, _ in calls] == ['read_note', 'search', 'read_note', 'write_note']
    assert seen[0] == seen[1] == seen[2] == seen[3] == s.tool_definitions


def test_unrelated_successful_read_does_not_unlock_a_dependent_write(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS)
    script = iter([[{'tool_calls': [tool_call('calculate', {'expression': 1})]}],
                   [{'tool_calls': [tool_call('read_note', {'name': 'other'})]}],
                   [{'tool_calls': [tool_call('write_note', {'content': 'wrong'})]}],
                   ['Calculation unresolved; no note written.']])
    s = start_session(monkeypatch, tools=tools, chat=lambda *a, **k: iter(next(script)))
    assert list(s.ask('calculate and save'))[-1] == Text('Calculation unresolved; no note written.')
    assert tools.calls == [('read_note', {'name': 'other'})]


def test_confirmed_write_rejection_can_reread_then_correct_without_pending_lock(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS)
    calls = []
    def dispatch(name, args):
        calls.append((name, args))
        return rejected() if name == 'write_note' and args.get('note') == 'stale' else ToolResult('ok')
    tools.call_tool_result = dispatch
    script = iter([[{'tool_calls': [tool_call('write_note', {'note': 'stale', 'content': 'new'})]}],
                   [{'tool_calls': [tool_call('read_note', {'name': 'fresh'})]}],
                   [{'tool_calls': [tool_call('write_note', {'note': 'fresh', 'content': 'new'})]}], ['saved']])
    s = start_session(monkeypatch, tools=tools, chat=lambda *a, **k: iter(next(script)))
    assert list(s.ask('replace'))[-1] == Text('saved')
    assert [n for n, _ in calls] == ['write_note', 'read_note', 'write_note']
    assert not s.pending_writes()
    assert s.store._conn.execute('select state from write_attempts order by id').fetchall() == [('not_completed',), ('completed',)]
    assert 'confirmed not completed' in s._partial_actions()


def test_recovery_reads_are_bounded_even_when_successful(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS)
    script = iter([[{'tool_calls': [tool_call('calculate', {'expression': 1})]}],
                   [{'tool_calls': [tool_call('read_note', {'name': 'a'})]}],
                   [{'tool_calls': [tool_call('search', {'query': 'b'})]}], ['calculation unresolved']])
    seen = []
    def chat(*a, **kw):
        seen.append(kw.get('tools'))
        return iter(next(script))
    s = start_session(monkeypatch, tools=tools, chat=chat)
    assert list(s.ask('compute'))[-1] == Text('calculation unresolved')
    assert seen[-1] == s.tool_definitions and len(tools.calls) == 2


def test_recovery_read_failure_does_not_unlock_rejected_write(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS)
    def dispatch(name, args):
        tools.calls.append((name, args))
        return rejected() if name == 'write_note' else ToolResult('not found', True)
    tools.call_tool_result = dispatch
    script = iter([[{'tool_calls': [tool_call('write_note', {'content': 'new'})]}],
                   [{'tool_calls': [tool_call('read_note', {'name': 'missing'})]}],
                   [{'tool_calls': [tool_call('write_note', {'content': 'new'})]}],
                   ['Missing evidence; no write retried.']])
    s = start_session(monkeypatch, tools=tools, chat=lambda *a, **k: iter(next(script)))
    assert list(s.ask('write'))[-1] == Text('Missing evidence; no write retried.')
    assert [n for n, _ in tools.calls] == ['write_note', 'read_note']

def test_mcp_sdk_prefixed_rejection_receipt_is_recognized():
    raw = rejected()
    result = ToolResult('Error executing tool write_note: ' + raw.text, True)
    outcome = tool_dispatch.classify('write_note', result)
    assert outcome.certainty == 'not_completed' and outcome.repairable
    wrong = ToolResult('Error executing tool rename_or_move_file: ' + raw.text, True)
    assert tool_dispatch.classify('write_note', wrong).certainty == 'uncertain'

def test_recovery_exhaustion_rejects_hallucinated_tool_without_step_enforcement(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS)
    script = iter([[{'tool_calls': [tool_call('calculate', {'expression': 1})]}],
                   [{'tool_calls': [tool_call('calculate', {'expression': 2})]}],
                   [{'tool_calls': [tool_call('calculate', {'expression': 3})]}],
                   [{'tool_calls': [tool_call('write_note', {'content': 'unverified'})]}],
                   [{'tool_calls': [tool_call('write_note', {'content': 'unverified'})]}]])
    s = start_session(monkeypatch, tools=tools, chat=lambda *a, **k: iter(next(script)))
    s.enforcement = None
    events = list(s.ask('compute and save'))
    assert not tools.calls
    assert any(isinstance(e, Text) and 'recovery budget' in e.text for e in events)


def test_exhausted_recovery_answers_without_tier_escalation(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS)
    script = iter([[{'tool_calls': [tool_call('calculate', {'expression': 1})]}],
                   [{'tool_calls': [tool_call('calculate', {'expression': 2})]}],
                   [{'tool_calls': [tool_call('calculate', {'expression': 3})]}],
                   ['No verified calculation; nothing written.']])
    s = start_session(monkeypatch, tools=tools, chat=lambda *a, **k: iter(next(script)))
    monkeypatch.setattr(s.enforcement, 'on', lambda point: point == 'watch_thinking')
    original = s.enforcement.think_for
    calls = []
    def think_for(*args):
        calls.append(True)
        assert len(calls) <= 3, 'exhausted recovery must bypass tier escalation'
        return original(*args)
    monkeypatch.setattr(s.enforcement, 'think_for', think_for)
    assert list(s.ask('compute'))[-1] == Text('No verified calculation; nothing written.')
    assert len(calls) == 3 and not tools.calls


def test_duplicate_recovery_call_requests_answer_instead_of_ending_turn(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS)
    tools.call_tool_result = lambda *a: ToolResult('{"ok":false,"status":"SCRIPT_ERROR","stderr":"bad fit"}')
    script = iter([[{'tool_calls': [tool_call('calculate', {'expression': 'bad'})]}],
                   [{'tool_calls': [tool_call('calculate', {'expression': 'bad'})]}],
                   ['Could not calculate; no note written.']])
    s = start_session(monkeypatch, tools=tools, chat=lambda *a, **k: iter(next(script)))
    repeats = iter([None, 'repeated failed call'])
    monkeypatch.setattr(s, '_repeated_call', lambda *a: next(repeats))
    assert list(s.ask('compute'))[-1] == Text('Could not calculate; no note written.')


def test_exhausted_recovery_finishes_reasoning_only_answer_without_retry(monkeypatch):
    tools = FakeMCPTools(tools=TOOLS)
    script = iter([[{'tool_calls': [tool_call('calculate', {'expression': 1})]}],
                   [{'tool_calls': [tool_call('calculate', {'expression': 2})]}],
                   [{'tool_calls': [tool_call('calculate', {'expression': 3})]}],
                   [{'thinking': 'The calculation remains unresolved.'}]])
    seen = []
    def chat(*a, **k):
        seen.append(k.get('tools'))
        return iter(next(script))
    s = start_session(monkeypatch, tools=tools, chat=chat)
    s.enforcement = None
    monkeypatch.setattr(session, 'THINK', True)
    assert list(s.ask('compute'))[-1] == Text(session._NO_REPLY_NOTE)
    assert seen == [s.tool_definitions] * 4
    assert not tools.calls


@pytest.mark.parametrize("outcome, message", [
    (tool_dispatch.failure("transport", "TimeoutError", "no answer", certainty="uncertain"),
     "The outcome of write_note is uncertain: no answer. Do not retry the write."),
    (tool_dispatch.failure("replay", "WRITE_BLOCKED", "an earlier write is uncertain"),
     "Further writes were blocked: an earlier write is uncertain."),
    (tool_dispatch.failure("internal", "KeyError", ""), "write_note stopped because of an internal error: KeyError."),
    (tool_dispatch.failure("arguments", "MISSING_NOTE", "note is required"), "write_note could not be completed: note is required."),
])
def test_each_kind_of_failure_tells_the_user_what_happened(outcome, message):
    """F13: what the user is told; an uncertain write says not to retry it."""
    assert outcome.user_message("write_note") == message
