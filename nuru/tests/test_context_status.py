"""F18: disposable model streams and stores; no live models or vaults."""
import copy
import dataclasses
import threading

import pytest
import requests

from core import session
from core.context_budget import available_tokens, input_budget
from core.session import ContextStatus, Text
from core.service import protocol
from test_session import start_session, FakeMCPTools, tool_call, _isolate_model_settings
from test_service_host import harness, ME, SOMEONE_ELSE


@pytest.fixture
def chat_session(monkeypatch):
    s = start_session(monkeypatch, tools=FakeMCPTools(tools=[]))
    monkeypatch.setattr(s, '_system_text', lambda: '')
    yield s
    s.close()


def collect(s, monkeypatch, usage, *, claude=False, messages=None, window=1000):
    seen, statuses = [], []
    def chat(model, sent, **kwargs):
        seen.append(copy.deepcopy(sent))
        yield 'OK'
        yield usage
    monkeypatch.setattr(s, '_chat_with', chat)
    monkeypatch.setattr(s, '_claude', lambda: claude)
    s.on_context = statuses.append
    list(s._chat('test', messages or [{'role': 'user', 'content': 'hello'}], num_ctx=window))
    return seen, statuses


def test_cached_local_counter_cannot_make_a_large_sent_prompt_look_empty(chat_session, monkeypatch):
    messages = [{'role': 'user', 'content': 'A long useful passage. ' * 100}]
    sent, statuses = collect(chat_session, monkeypatch, {'prompt_tokens': 1, 'completion_tokens': 9}, messages=messages, window=8192)
    result = statuses[-1]
    expected = input_budget(8192) - available_tokens(8192, sent[0]) + 9
    assert result.used_tokens == expected and result.used_tokens > 400
    assert result.estimated and result.phase == 'finished'
    assert not result.history_omitted and not result.tool_results_omitted


@pytest.mark.parametrize('usage,claude,expected', [
    ({'prompt_tokens': 111, 'completion_tokens': 9, 'cache_read_tokens': 90, 'cache_write_tokens': 10}, True, 120),
    ({'prompt_tokens': 0, 'completion_tokens': 0}, True, 0),
    ({'prompt_tokens': 1, 'completion_tokens': 0, 'usage_available': False}, False, None),
    ({'prompt_tokens': None, 'completion_tokens': 2}, True, None),
    ({'prompt_tokens': 1, 'completion_tokens': None}, False, None),
    ({'prompt_tokens': 1, 'completion_tokens': -1}, False, None),
    ({'prompt_tokens': 1, 'completion_tokens': True}, False, None),
    ({'prompt_tokens': 1, 'completion_tokens': 1_000_000_001}, False, None),
    ({'prompt_tokens': 1_000_000_000, 'completion_tokens': 1}, True, None),
])
def test_normalized_claude_cache_counted_once_and_unknown_is_not_zero(chat_session, monkeypatch, usage, claude, expected):
    _, statuses = collect(chat_session, monkeypatch, usage, claude=claude)
    assert statuses[-1].used_tokens == expected
    assert statuses[-1].estimated is not claude


def test_small_window_reports_real_history_and_evidence_omission_without_rewriting(chat_session, monkeypatch):
    s = chat_session
    s.store.add_message(s.conversation_id, 'user', 'old words ' * 400)
    s.store.add_message(s.conversation_id, 'nuru', 'old answer ' * 400)
    original = s.store.get_messages(s.conversation_id)
    statuses = []
    s.on_context = statuses.append
    assert s._history([{'role': 'user', 'content': 'Now?'}], 1000) == []
    assert statuses[-1].history_omitted
    messages = [{'role': 'user', 'content': 'Now?'}, {'role': 'tool', 'content': 'evidence ' * 1500}]
    before = copy.deepcopy(messages)
    sent, statuses = collect(s, monkeypatch, {'prompt_tokens': 1, 'completion_tokens': 2}, messages=messages)
    assert statuses[-1].history_omitted and statuses[-1].tool_results_omitted
    assert 'left out' in sent[0][-1]['content']
    assert messages == before and s.store.get_messages(s.conversation_id) == original
    assert statuses[-1].used_tokens < 1000
    # A later call does not erase the sticky fact or inflate an item count.
    _, statuses = collect(s, monkeypatch, {'prompt_tokens': 1, 'completion_tokens': 2})
    assert statuses[-1].history_omitted and statuses[-1].tool_results_omitted


def test_fitting_history_in_chat_reports_eviction_even_if_initial_selection_did_not(chat_session, monkeypatch):
    s = chat_session
    s.on_context = lambda _: None
    messages = [{'role': 'user', 'content': 'old ' * 600}, {'role': 'assistant', 'content': 'old ' * 600},
                {'role': 'user', 'content': 'Now?'}]
    monkeypatch.setattr(s, '_chat_with', lambda *_args, **_kw: iter([{'prompt_tokens': 1, 'completion_tokens': 1}]))
    list(s._chat('local', messages, num_ctx=1000, history_count=2))
    assert s._context.history_omitted and not s._context.tool_results_omitted


def test_last_call_usage_replaces_not_sums_and_model_switch_clears_it(chat_session, monkeypatch):
    s = chat_session
    _, first = collect(s, monkeypatch, {'prompt_tokens': 10, 'completion_tokens': 2}, claude=True)
    _, second = collect(s, monkeypatch, {'prompt_tokens': 20, 'completion_tokens': 3}, claude=True)
    assert second[0].phase == 'calling' and second[0].used_tokens == 12
    assert second[-1].used_tokens == 23
    statuses = []
    s.on_context = statuses.append
    list(s._chat('other-model', [{'role': 'user', 'content': 'Hi'}], num_ctx=2000))
    assert statuses[0].used_tokens is None and statuses[0].window_tokens == 2000


def test_actual_overflow_replacement_has_its_own_omission_signal(monkeypatch):
    calls = []
    def chat(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            yield {'tool_calls': [tool_call('search', {})]}
        elif len(calls) == 2:
            response = requests.Response(); response.status_code = 400
            response._content = b'request (999 tokens) exceeds the available context size'
            raise requests.HTTPError(response=response)
        else:
            yield 'Done'; yield {'prompt_tokens': 10, 'completion_tokens': 1}
    s = start_session(monkeypatch, chat=chat, tools=FakeMCPTools(tool_results={'search': 'Evidence ' * 50}))
    statuses = []; s.on_context = statuses.append
    try:
        list(s.ask('Search'))
        assert len(calls) == 3 and statuses[-1].tool_results_omitted
    finally: s.close()


def test_turn_reset_failure_and_cancellation_do_not_reuse_previous_context(chat_session, monkeypatch):
    s = chat_session
    collect(s, monkeypatch, {'prompt_tokens': 1, 'completion_tokens': 1})
    s._report_context(history_omitted=True, tool_results_omitted=True)
    def broken(*_args, **_kwargs):
        raise requests.ConnectionError('offline')
        yield
    monkeypatch.setattr(s, '_chat_with', broken)
    statuses = []; s.on_context = statuses.append
    assert any(isinstance(e, session.Failure) for e in s.ask('next'))
    assert statuses[0] == ContextStatus()
    assert s._context.used_tokens is None and not s._context.history_omitted
    closed = []
    def waiting(*_args, **_kwargs):
        try: yield 'partial'
        finally: closed.append(True)
    monkeypatch.setattr(s, '_chat_with', waiting)
    events = s.ask('again')
    assert isinstance(next(events), Text)
    events.close()
    assert closed == [True] and s._context.used_tokens is None


def test_host_stream_persists_context_and_compacted_history_keeps_it(harness):
    def script(s, text, cancel):
        s.on_context(ContextStatus(window_tokens=1000, used_tokens=300, phase='finished', history_omitted=True))
        yield Text('Answer')
    h = harness([script])
    cid = h.conversation(); _, body = h.submit(cid)
    events = h.finished(body['turn_id'])
    protocol.check_stream(events)
    assert next(e for e in events if e['type'] == 'status')['data']['context']['used_tokens'] == 300
    shown = h.service.conversation(ME, cid, None, 10)['turns'][0]['display']
    assert isinstance(shown['context'], dict)
    assert shown['context']['history_omitted']
    with h.service._lock, h.service.store.transaction():
        h.service.store._conn.execute('DELETE FROM events WHERE turn_id=?', (body['turn_id'],))
    h.close(); h.service = h.open()
    assert h.service.conversation(ME, cid, None, 10)['turns'][0]['display']['context'] == shown['context']
    with pytest.raises(protocol.ProtocolError): h.service.conversation(SOMEONE_ELSE, cid, None, 10)


@pytest.mark.parametrize('changes', [{'used_tokens': -1}, {'used_tokens': True}, {'window_tokens': 0},
                                    {'phase': 'lying'}, {'private': 'prompt text'}])
def test_context_schema_refuses_invalid_or_content_bearing_status(changes):
    from core.service.protocol import TurnStream
    stream = TurnStream(protocol.new_id(), protocol.new_id())
    stream.emit('accepted', {'request_id': protocol.new_id()}); stream.emit('started', {})
    with pytest.raises(protocol.ProtocolError):
        stream.emit('status', {'context': {**dataclasses.asdict(ContextStatus()), **changes}})


def test_small_window_turn_includes_both_real_omissions(monkeypatch):
    seen = []
    def chat(_model, messages, **kwargs):
        seen.append(copy.deepcopy(messages))
        if len(seen) == 1: yield {'tool_calls': [tool_call('search', {})]}
        else: yield 'Answer'
        yield {'prompt_tokens': 1, 'completion_tokens': 2}
    s = start_session(monkeypatch, chat=chat, model_info={'context_window': 1200},
                      tools=FakeMCPTools(tool_results={'search': 'evidence ' * 2000}))
    monkeypatch.setattr(s, '_system_text', lambda: '')
    s.store.add_message(s.conversation_id, 'user', 'old history ' * 2000)
    s.store.add_message(s.conversation_id, 'nuru', 'old answer ' * 2000)
    statuses = []; s.on_context = statuses.append
    try:
        assert any(isinstance(e, Text) and e.text == 'Answer' for e in s.ask('Search'))
        assert statuses[-1].history_omitted and statuses[-1].tool_results_omitted
        assert statuses[-1].used_tokens < 1200
        assert 'left out' in next(m['content'] for m in seen[-1] if m['role'] == 'tool')
    finally: s.close()
