import sqlite3

import pytest

from core.context_budget import estimate_tokens
from core.conversation_store import _MAX_HISTORY_MESSAGES, ConversationStore


def test_creates_db_file_and_parent_dirs_if_missing(tmp_path):
    db_path = tmp_path / "nested" / "conversation.db"
    assert not db_path.parent.exists()

    ConversationStore(db_path)

    assert db_path.is_file()


def test_create_conversation_returns_incrementing_ids(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")

    first = store.create_conversation()
    second = store.create_conversation(title="session 2")

    assert first == 1
    assert second == 2


def test_add_message_stores_role_content_and_tokens(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()

    store.add_message(conversation_id, "user", "hello there", None)
    store.add_message(conversation_id, "assistant", "hi!", tokens_used=12)

    messages = store.get_messages(conversation_id)
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hello there"
    assert messages[0]["tokens_used"] is None
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "hi!"
    assert messages[1]["tokens_used"] == 12


def test_update_tokens_sets_tokens_used_on_existing_message(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    message_id = store.add_message(conversation_id, "user", "hello there")

    store.update_tokens(message_id, 42)

    messages = store.get_messages(conversation_id)
    assert messages[0]["tokens_used"] == 42


def test_delete_message_removes_it(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    keep_id = store.add_message(conversation_id, "user", "keep me")
    delete_id = store.add_message(conversation_id, "user", "delete me")

    store.delete_message(delete_id)

    messages = store.get_messages(conversation_id)
    assert [m["id"] for m in messages] == [keep_id]


def test_get_latest_prompt_tokens_falls_back_after_a_deleted_failed_turn(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    store.add_message(conversation_id, "user", "first", tokens_used=500)
    failed_id = store.add_message(conversation_id, "user", "second, but it failed")

    store.delete_message(failed_id)

    assert store.get_latest_prompt_tokens(conversation_id) == 500


def test_get_latest_prompt_tokens_returns_none_when_no_user_message_exists(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()

    assert store.get_latest_prompt_tokens(conversation_id) is None


def test_get_latest_prompt_tokens_returns_the_most_recent_user_messages_tokens(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    store.add_message(conversation_id, "user", "first", tokens_used=100)
    store.add_message(conversation_id, "nuru", "reply", tokens_used=20)
    store.add_message(conversation_id, "user", "second", tokens_used=250)

    assert store.get_latest_prompt_tokens(conversation_id) == 250


def test_get_latest_prompt_tokens_ignores_other_conversations(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_a = store.create_conversation()
    conversation_b = store.create_conversation()
    store.add_message(conversation_a, "user", "a", tokens_used=111)
    store.add_message(conversation_b, "user", "b", tokens_used=222)

    assert store.get_latest_prompt_tokens(conversation_a) == 111
    assert store.get_latest_prompt_tokens(conversation_b) == 222


def test_get_recent_messages_returns_all_when_under_budget(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    store.add_message(conversation_id, "user", "first", tokens_used=10)
    store.add_message(conversation_id, "assistant", "second", tokens_used=10)
    store.add_message(conversation_id, "user", "third", tokens_used=10)

    messages = store.get_recent_messages(conversation_id, token_budget=100)

    assert [m["content"] for m in messages] == ["first", "second"]


def test_history_uses_content_not_request_usage(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    cid = store.create_conversation()
    first = store.add_message(cid, "user", "x" * 240, tokens_used=6000)
    reply = store.add_message(cid, "nuru", "y" * 160, tokens_used=4000)
    current = store.add_message(cid, "user", "follow up")
    rows = store.get_recent_messages(cid, 5000, before_id=current)
    assert [m["id"] for m in rows] == [first, reply]
    assert sum(m["content_tokens"] for m in rows) == 100
    assert store.get_latest_prompt_tokens(cid) is None
    store.update_tokens(current, 7000)
    assert store.get_latest_prompt_tokens(cid) == 7000
    assert store.get_messages(cid)[0]["content_tokens"] == 60


@pytest.mark.parametrize("budget", [-1, 0, 1, 100])
def test_never_forces_oversized_exchange_into_history(tmp_path, budget):
    store = ConversationStore(tmp_path / "conversation.db")
    cid = store.create_conversation()
    store.add_message(cid, "user", "x" * 1000)
    store.add_message(cid, "nuru", "y" * 1000)
    assert store.get_recent_messages(cid, budget) == []


def test_history_skips_orphans_and_preserves_empty_replies(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    cid = store.create_conversation()
    store.add_message(cid, "nuru", "orphan")
    store.add_message(cid, "user", "abandoned")
    user = store.add_message(cid, "user", "cancelled")
    reply = store.add_message(cid, "nuru", "")
    store.add_message(cid, "user", "current")
    assert [m["id"] for m in store.get_recent_messages(cid, 100)] == [user, reply]


def test_history_drops_whole_oldest_exchange(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    cid = store.create_conversation()
    for content in ("old", "new"):
        store.add_message(cid, "user", content * 20)
        store.add_message(cid, "nuru", content * 20)
    assert [m["content"] for m in store.get_recent_messages(cid, 62)] == ["new" * 20] * 2


def test_get_recent_messages_never_exceeds_the_hard_history_cap(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    cid = store.create_conversation()
    for i in range(_MAX_HISTORY_MESSAGES + 10):
        store.add_message(cid, "user" if i % 2 == 0 else "nuru", f"message {i}")
    rows = store.get_recent_messages(cid, 10**9)
    assert len(rows) == _MAX_HISTORY_MESSAGES
    assert rows[0]["content"] == "message 10"


def legacy_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE conversations (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, title TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id INTEGER, timestamp TEXT,
                               role TEXT, content TEXT, tokens_used INTEGER);
        INSERT INTO conversations VALUES (1, 'now', 'old');
    """)
    conn.executemany("INSERT INTO messages VALUES (?, 1, 'same', ?, ?, ?)",
                     [(i, "user" if i % 2 else "nuru", "ü" * i, 6000) for i in range(1, 601)])
    conn.commit()
    conn.close()


def test_migration_preserves_legacy_rows_and_is_idempotent(tmp_path):
    path = tmp_path / "old.db"
    legacy_db(path)
    for _ in range(2):
        store = ConversationStore(path)
        rows = store.get_messages(1)
        assert len(rows) == 600
        for i, row in enumerate(rows, 1):
            assert row == dict(id=i, conversation_id=1, timestamp="same", role="user" if i % 2 else "nuru",
                              content="ü" * i, tokens_used=6000, content_tokens=estimate_tokens("ü" * i))
        assert store._conn.execute("SELECT version FROM conversation_migrations").fetchall() == [(1,), (2,)]
        assert {"call_id", "target"} <= {row[1] for row in store._conn.execute("PRAGMA table_info(write_attempts)")}
        store.close()


def test_migration_rolls_back_schema_and_partial_backfill(tmp_path, monkeypatch):
    import core.conversation_store as module
    path = tmp_path / "old.db"
    legacy_db(path)
    def fail_later(content):
        if len(content) > 300:
            raise RuntimeError("interrupted")
        return estimate_tokens(content)
    with monkeypatch.context() as patch:
        patch.setattr(module, "estimate_tokens", fail_later)
        with pytest.raises(RuntimeError, match="interrupted"):
            ConversationStore(path)
    with sqlite3.connect(path) as conn:
        assert "content_tokens" not in {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE tokens_used = 6000").fetchone()[0] == 600
    store = ConversationStore(path)
    assert len(store.get_messages(1)) == 600


def test_get_messages_only_returns_messages_for_given_conversation(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_a = store.create_conversation()
    conversation_b = store.create_conversation()

    store.add_message(conversation_a, "user", "a message")
    store.add_message(conversation_b, "user", "b message")

    messages_a = store.get_messages(conversation_a)
    messages_b = store.get_messages(conversation_b)

    assert [m["content"] for m in messages_a] == ["a message"]
    assert [m["content"] for m in messages_b] == ["b message"]


def test_get_messages_are_ordered_by_timestamp(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()

    store.add_message(conversation_id, "user", "first")
    store.add_message(conversation_id, "assistant", "second")
    store.add_message(conversation_id, "user", "third")

    messages = store.get_messages(conversation_id)

    assert [m["content"] for m in messages] == ["first", "second", "third"]


def test_data_persists_across_store_instances(tmp_path):
    db_path = tmp_path / "conversation.db"
    store = ConversationStore(db_path)
    conversation_id = store.create_conversation(title="persisted")
    store.add_message(conversation_id, "user", "hello")
    store.close()

    reopened = ConversationStore(db_path)
    messages = reopened.get_messages(conversation_id)

    assert [m["content"] for m in messages] == ["hello"]


@pytest.mark.parametrize("call, message", [
    (lambda store: store.finish_write(1, "maybe"), "invalid dispatched-write state"),
    (lambda store: store.resolve_write(1, "vault", "uncertain"), "resolution must be completed or not_completed"),
])
def test_a_write_state_outside_the_ledgers_states_is_refused(tmp_path, call, message):
    """F13: the write ledger only takes its own states, and an operator can't 'resolve' to uncertain."""
    store = ConversationStore(tmp_path / "conversation.db")
    try:
        with pytest.raises(ValueError, match=message):
            call(store)
    finally:
        store.close()
