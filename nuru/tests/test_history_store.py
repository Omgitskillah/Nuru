"""R14 storage foundation, disposable SQLite only: no endpoints, models or purge scheduler."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from core.service.store import ServiceStore
from core.service import store as module

NOW = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)

@pytest.fixture
def store(tmp_path):
    value = ServiceStore(tmp_path / 'history.db')
    yield value
    value.close()


def seed(store, cid='a', principal='owner', title='Title', prompt='Prompt', answer='Answer', state='completed'):
    store.add_conversation(cid, principal, 1, title)
    if prompt is not None:
        tid = 'turn-' + cid
        store.add_turn(tid, cid, 'request-' + cid, prompt)
        if state in ('completed', 'failed', 'cancelled'):
            store.end_turn(tid, state, {'reply': answer, 'sources': [{'path': 'note.md'}]})
    return cid


def test_old_database_migration_preserves_content_and_reopens(tmp_path, monkeypatch):
    path = tmp_path / 'old.db'
    with monkeypatch.context() as patch:
        patch.setattr(module, '_MIGRATIONS', {k:v for k,v in module._MIGRATIONS.items() if k < 5})
        old = ServiceStore(path)
        seed(old, title='Straße')
        old.close()
    for _ in range(2):
        current = ServiceStore(path)
        try:
            state = current.history_state('a', 'owner')
            assert state['title'] == 'Straße' and state['title_rev'] == state['title_by_user'] == state['purging'] == 0
            assert state['hidden_at'] is state['purged_at'] is None
            assert current.turns('a', None, 1)[0]['final']['reply'] == 'Answer'
            assert current.history('owner', query='STRASSE')[0]['id'] == 'a'
        finally:
            current.close()


def test_rename_same_text_advances_revision_and_blocks_late_generation(store):
    seed(store, title='Prompt')
    old = store.history_state('a', 'owner')
    assert store.rename_history('a', 'intruder', old['title_rev'], 'Stolen') is False
    assert store.rename_history('a', 'owner', old['title_rev'], 'Prompt') is True
    new = store.history_state('a', 'owner')
    assert new['title_rev'] == 1 and new['title_by_user'] == 1
    assert not store.retitle('a', 'Prompt', 'Late generated title', expected_revision=0)
    assert not store.rename_history('a', 'owner', 0, 'Stale UI')
    assert store.rename_history('a', 'owner', 1, 'Pr')
    assert store.history_state('a', 'owner')['title_rev'] == 2
    assert store.retitle('a', 'Pr', 'Explicit generated title', expected_revision=2)
    assert store.history_state('a', 'owner')['title_rev'] == 3
    assert store.history_state('a', 'intruder') is None
    assert store.history_state('missing', 'owner') is None


@pytest.mark.parametrize('title', ['', '  ', 'x'*201, 'bad\nname', 'bad\x7f', 'bad\ud800', None, 4])
def test_invalid_titles_never_change_state(store, title):
    seed(store)
    with pytest.raises(ValueError): store.rename_history('a', 'owner', 0, title)
    assert store.title('a') == 'Title'


@pytest.mark.parametrize('revision', [-1, True, 0.0, '0', None])
def test_invalid_revisions_are_not_coerced(store, revision):
    seed(store)
    with pytest.raises(ValueError): store.rename_history('a', 'owner', revision, 'Name')


def test_search_is_literal_unicode_and_includes_only_title_prompt_reply(store):
    seed(store, 'title', title='Straße trip', prompt='Nothing', answer='Else')
    seed(store, 'prompt', title='Other', prompt='Meet STRASSE', answer='Else')
    seed(store, 'reply', title='Other', prompt='Nothing', answer='About straße')
    seed(store, 'symbol', title='Other', prompt='Use 50%_literal', answer='Else')
    seed(store, 'intruder', principal='intruder', title='STRASSE')
    seed(store, 'empty', title='STRASSE', prompt=None)
    seed(store, 'tool-only', title='Other', prompt='Nothing', answer='Else')
    store._conn.execute("UPDATE turns SET final = ? WHERE conversation_id = 'tool-only'", (json.dumps({'reply':'Else','reasoning':'STRASSE'}),))
    assert [x['id'] for x in store.history('owner', query='STRASSE')] == ['reply','prompt','title']
    assert [x['id'] for x in store.history('owner', query='%_')] == ['symbol']
    assert store.history('owner', query="' OR 1=1 --") == []
    assert store.history('owner', query='[') == []
    assert all(x['id'] not in ('empty','intruder') for x in store.history('owner'))


def test_search_pagination_is_bounded_and_wrong_owner_cursor_does_not_leak(store):
    for i in range(5): seed(store, str(i))
    seed(store, 'foreign', principal='intruder')
    assert [x['id'] for x in store.history('owner', limit=2)] == ['4','3']
    assert [x['id'] for x in store.history('owner', before='3', limit=2)] == ['2','1']
    assert store.history('owner', before='foreign') == []
    assert store.history('owner', before='missing') == []


@pytest.mark.parametrize('limit', [0, 51, True, '2'])
def test_invalid_bounds(store, limit):
    with pytest.raises(ValueError): store.history('owner', limit=limit)
    with pytest.raises(ValueError): store.due_history(NOW, limit)


@pytest.mark.parametrize('query,hidden', [(None,False),('x'*257,False),('bad\ud800',False),('',1)])
def test_invalid_filters(store, query, hidden):
    with pytest.raises(ValueError): store.history('owner', query=query, hidden=hidden)


def test_hide_undo_and_exact_expiry_preserve_rows_messages_and_receipts(store):
    seed(store)
    store.add_request('key','create_conversation','req', 'hash',201,{'conversation_id':'a'},'a',None)
    assert not store.hide_history('a','intruder',NOW)
    assert store.hide_history('a','owner',NOW)
    assert not store.hide_history('a','owner',NOW + timedelta(days=1)), 'retries must not reset retention'
    assert store.history('owner') == []
    assert store.history('owner', hidden=True)[0]['id'] == 'a'
    assert not store.rename_history('a','owner',0,'Hidden rename')
    assert not store.undo_hide('a','intruder',NOW)
    assert store.undo_hide('a','owner',NOW + timedelta(days=30) - timedelta(milliseconds=1))
    assert store.hide_history('a','owner',NOW + timedelta(days=1))
    boundary = NOW + timedelta(days=31)
    assert store.due_history(boundary - timedelta(milliseconds=1)) == []
    assert store.due_history(boundary) == [{'id':'a','principal':'owner','store_id':1}]
    assert not store.undo_hide('a','owner',boundary)
    assert store.request('key')['body'] == {'conversation_id':'a'}
    assert store.turns('a',None,1)[0]['text'] == 'Prompt', 'foundation must not purge content'


@pytest.mark.parametrize('state', ['accepted','running','queued'])
def test_active_turns_cannot_be_hidden(store, state):
    seed(store, state=state)
    store.set_turn_state('turn-a', state)
    assert not store.hide_history('a','owner',NOW)
    assert store.history('owner')[0]['hidden_at'] is None


@pytest.mark.parametrize('field,value', [('purging',1),('purged_at','2026-11-01T00:00:00.000Z')])
def test_purge_states_are_not_visible_renameable_or_undoable(store, field, value):
    seed(store)
    store._conn.execute(f'UPDATE conversations SET {field} = ? WHERE id = ?', (value,'a'))
    assert store.history('owner') == []
    assert not store.rename_history('a','owner',0,'Name')
    assert not store.hide_history('a','owner',NOW)
    assert not store.undo_hide('a','owner',NOW)
    if field == 'purged_at': assert store.due_history(NOW) == []


def test_history_clock_requires_timezone(store):
    seed(store)
    for call in [lambda: store.hide_history('a','owner',NOW.replace(tzinfo=None)),
                 lambda: store.undo_hide('a','owner',NOW.replace(tzinfo=None)),
                 lambda: store.due_history(NOW.replace(tzinfo=None))]:
        with pytest.raises(ValueError): call()
