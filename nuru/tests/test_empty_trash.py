"""Confirmed erasure on disposable databases; no real vault, model or filesystem removal."""
import pytest
from core.service import protocol
from core.service.protocol import ProtocolError
from test_service_host import harness, seed_old_conversation, ME, SOMEONE_ELSE


def trash(h, cid):
    h.service.change_history(ME, cid, 'hide_history', {'request_id': protocol.new_id()})
    row = h.service.store.history_state(cid, ME)
    return {'id': cid, 'trash_rev': row['trash_rev']}


def request(*targets):
    return {'request_id': protocol.new_id(), 'confirmed': True, 'targets': list(targets)}


def test_empty_scrubs_both_stores_but_preserves_identity_receipts_and_write_evidence(harness, monkeypatch):
    h = harness([])
    cid = seed_old_conversation(h)
    sid = h.service.store.history_state(cid, ME)['store_id']
    h.service.conversations.add_message(sid, 'user', 'Private message')
    write = h.service.conversations.begin_write(sid, 'turn-evidence', str(h.vault), 'write_note', {'content':'Required evidence'})
    h.service.conversations.finish_write(write, 'uncertain', 'Keep this diagnostic')
    evidence = h.service.conversations.pending_writes(str(h.vault))
    h.service.store.add_request('old-content', 'answer_question', 'old-request', 'binding', 200,
                                {'v':1, 'conversation_id':cid, 'private':'Remove cached content'}, cid)
    other = h.conversation(principal=SOMEONE_ELSE, request_id=protocol.new_id())
    target = trash(h, cid)
    payload = request(target)
    status, receipt = h.service.empty_trash(ME, payload)
    assert status == 200 and receipt['erased'] == 1 and receipt['state'] == 'completed' and receipt['skipped'] == []
    state = h.service.store.history_state(cid, ME)
    assert state['purged_at'] and state['title'] is None
    assert h.service.store.turns(cid, None, 1)[0]['text'] == ''
    assert h.service.store.turns(cid, None, 1)[0]['final'] is None
    assert h.service.conversations._conn.execute('SELECT content FROM messages WHERE conversation_id=?',(sid,)).fetchall() == []
    assert h.service.conversations._conn.execute('SELECT title FROM conversations WHERE id=?',(sid,)).fetchone() == (None,)
    assert h.service.conversations.pending_writes(str(h.vault)) == evidence
    assert h.service.store.request('old-content')['payload_hash'] == 'binding'
    assert 'private' not in h.service.store.request('old-content')['body']
    assert h.service.store.conversation(other, SOMEONE_ELSE) is not None
    monkeypatch.setattr(h.service.conversations, 'erase_history', lambda *_: pytest.fail('replay erased again'))
    assert h.service.empty_trash(ME, payload) == (status, receipt)
    with pytest.raises(ProtocolError):
        h.service.change_history(ME, cid, 'undo_history', {'request_id':protocol.new_id()})


def test_snapshot_intersection_keeps_restored_rehidden_and_new_trash(harness):
    h = harness([])
    keep, erase, added = [seed_old_conversation(h) for _ in range(3)]
    stale, valid = trash(h, keep), trash(h, erase)
    h.service.change_history(ME, keep, 'undo_history', {'request_id':protocol.new_id()})
    trash(h, keep)
    trash(h, added)
    _, receipt = h.service.empty_trash(ME, request(stale, valid))
    assert receipt['erased'] == 1 and receipt['skipped'] == [{'id':keep,'reason':'changed'}]
    assert h.service.store.history_state(keep, ME)['purged_at'] is None
    assert h.service.store.history_state(added, ME)['purged_at'] is None


@pytest.mark.parametrize('phase', ['before_other_store', 'after_other_store', 'during_service_commit'])
def test_confirmed_erasure_recovers_after_each_cross_database_boundary(harness, monkeypatch, phase):
    h = harness([])
    cid = seed_old_conversation(h)
    sid = h.service.store.history_state(cid, ME)['store_id']
    h.service.conversations.add_message(sid, 'assistant', 'Erase after confirmation')
    payload = request(trash(h, cid))
    with monkeypatch.context() as patch:
        if phase == 'before_other_store':
            patch.setattr(h.service.conversations, 'erase_history', lambda *_: (_ for _ in ()).throw(OSError('crash')))
        else:
            original = h.service.store.finish_purge
            def crash(*args):
                if phase == 'during_service_commit': original(*args)
                raise OSError('crash')
            patch.setattr(h.service.store, 'finish_purge', crash)
        status, receipt = h.service.empty_trash(ME, payload)
        assert status == 202 and receipt['state'] == 'pending'
    assert h.service.store.history_state(cid, ME)['purging'] == 1
    assert h.service.store.turns(cid, None, 1)[0]['text'] == 'Original prompt'
    h.close()
    h.service = h.open()
    assert h.service.store.history_state(cid, ME)['purged_at']
    assert h.service.empty_trash(ME, payload)[1]['erased'] == 1
    assert h.service.store.pending_purges() == []


def test_confirmation_owner_auth_and_bounds_cannot_start_erasure(harness):
    h = harness([])
    cid = seed_old_conversation(h)
    target = trash(h, cid)
    payload = request(target)
    for bad in ({**payload,'confirmed':False}, request(target,target), {**payload,'targets':[target]*201}):
        with pytest.raises(ProtocolError): h.service.empty_trash(ME, bad)
    with pytest.raises(ProtocolError): h.service.empty_trash(ME, payload, authorize=lambda:False)
    assert h.service.empty_trash(SOMEONE_ELSE, payload)[1]['erased'] == 0
    assert h.service.store.history_state(cid, ME)['purging'] == 0
    assert h.service.store.history_state(cid, ME)['purged_at'] is None


def test_orphan_purging_mark_never_resumes_without_confirmed_receipt(harness):
    h = harness([])
    cid = seed_old_conversation(h)
    trash(h, cid)
    with h.service.store.transaction():
        h.service.store._conn.execute('UPDATE conversations SET purging=1 WHERE id=?',(cid,))
    h.close()
    h.service = h.open()
    assert h.service.store.history_state(cid, ME)['purged_at'] is None
    assert h.service.store.turns(cid, None, 1)[0]['text'] == 'Original prompt'


def test_intent_marks_and_consumed_request_commit_together(harness, monkeypatch):
    h = harness([])
    cid = seed_old_conversation(h)
    payload = request(trash(h,cid))
    with monkeypatch.context() as patch:
        patch.setattr(h.service.store, 'add_request', lambda *_: (_ for _ in ()).throw(OSError('commit failed')))
        with pytest.raises(OSError): h.service.empty_trash(ME,payload)
    assert h.service.store.history_state(cid,ME)['purging'] == 0
    assert h.service.store._conn.execute('SELECT COUNT(*) FROM purge_intents').fetchone()[0] == 0
    assert h.service.store.pending_purges() == []
    assert h.service.store.turns(cid,None,1)[0]['text'] == 'Original prompt'


def test_restored_and_busy_targets_are_skipped_while_eligible_target_is_erased(harness):
    h = harness([])
    restored,busy,eligible = [seed_old_conversation(h) for _ in range(3)]
    entries = [trash(h,cid) for cid in (restored,busy,eligible)]
    h.service.change_history(ME,restored,'undo_history',{'request_id':protocol.new_id()})
    with h.service._lock:
        h.service._title_active = busy
        try: _, receipt = h.service.empty_trash(ME,request(*entries))
        finally: h.service._title_active = None
    assert receipt['erased'] == 1
    assert receipt['skipped'] == [{'id':restored,'reason':'unavailable_or_restored'},{'id':busy,'reason':'busy'}]
    assert h.service.store.conversation(restored,ME)
    assert h.service.store.history_state(busy,ME)['purged_at'] is None


def test_finish_requires_a_committed_owned_target(harness):
    h = harness([])
    cid = seed_old_conversation(h)
    state = h.service.store.history_state(cid,ME)
    with pytest.raises(ValueError,match='no committed'):
        with h.service.store.transaction():
            h.service.store.finish_purge({'id':cid,'intent_key':'invented','principal':ME,'store_id':state['store_id']},h.service._clock())
    assert h.service.store.turns(cid,None,1)[0]['text'] == 'Original prompt'


def test_erasure_never_commits_another_workers_pending_transaction(harness):
    import sqlite3
    h = harness([])
    target,other = [seed_old_conversation(h) for _ in range(2)]
    target_sid = h.service.store.history_state(target,ME)['store_id']
    other_sid = h.service.store.history_state(other,ME)['store_id']
    db = h.service.conversations._conn
    db.execute('BEGIN IMMEDIATE')
    db.execute("INSERT INTO messages (conversation_id,timestamp,role,content) VALUES (?, 'now','user','Uncommitted')",(other_sid,))
    try:
        with pytest.raises(sqlite3.OperationalError,match='locked'):
            h.service.conversations.erase_history(target_sid)
        assert db.in_transaction, 'erasure must never commit a different worker transaction'
    finally:db.rollback()
    assert db.execute('SELECT content FROM messages WHERE conversation_id=?',(other_sid,)).fetchall() == []


def test_target_without_consumed_confirmation_never_resumes(harness):
    h = harness([])
    cid = seed_old_conversation(h)
    entry = trash(h,cid)
    sid = h.service.store.history_state(cid,ME)['store_id']
    h.service.conversations.add_message(sid,'user','Keep without a confirmed request')
    with h.service.store.transaction():
        h.service.store.add_purge_intent('uncommitted-request',ME,[entry])
        h.service.store._conn.execute('INSERT INTO purge_targets (intent_key,conversation_id,store_id) VALUES (?,?,?)',('uncommitted-request',cid,sid))
        h.service.store._conn.execute('UPDATE conversations SET purging=1 WHERE id=?',(cid,))
    h.close()
    h.service = h.open()
    assert h.service.store.pending_purges() == []
    assert h.service.store.turns(cid,None,1)[0]['text'] == 'Original prompt'

    assert h.service.conversations._conn.execute('SELECT content FROM messages WHERE conversation_id=?',(sid,)).fetchall() == [('Keep without a confirmed request',)]


def test_partial_progress_receipt_replays_without_retry_and_restart_finishes(harness,monkeypatch):
    h = harness([])
    first,second = [seed_old_conversation(h) for _ in range(2)]
    payload = request(trash(h,first),trash(h,second))
    calls=[]
    original=h.service.conversations.erase_history
    def fail_second(sid):
        calls.append(sid)
        if len(calls)>1:raise OSError('interruption')
        original(sid)
    with monkeypatch.context() as patch:
        patch.setattr(h.service.conversations,'erase_history',fail_second)
        status,receipt=h.service.empty_trash(ME,payload)
        assert status==202 and receipt['erased']==1 and receipt['state']=='pending'
        assert h.service.request_state(ME,payload['request_id'],'empty_trash')['state']=='accepted'
        assert h.service.empty_trash(ME,payload)==(status,receipt)
        assert len(calls)==2, 'a replay returns its receipt without retrying erasure'
    h.close()
    h.service=h.open()
    status,receipt=h.service.empty_trash(ME,payload)
    assert status==200 and receipt['erased']==2 and receipt['state']=='completed'
