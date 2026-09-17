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

    assert [m["content"] for m in messages] == ["first", "second", "third"]


def test_get_recent_messages_drops_oldest_once_budget_exceeded(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    store.add_message(conversation_id, "user", "first", tokens_used=50)
    store.add_message(conversation_id, "assistant", "second", tokens_used=50)
    store.add_message(conversation_id, "user", "third", tokens_used=50)

    messages = store.get_recent_messages(conversation_id, token_budget=100)

    assert [m["content"] for m in messages] == ["second", "third"]


def test_get_recent_messages_always_includes_at_least_the_latest_message(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    store.add_message(conversation_id, "user", "first", tokens_used=500)

    messages = store.get_recent_messages(conversation_id, token_budget=10)

    assert [m["content"] for m in messages] == ["first"]


def test_get_recent_messages_treats_null_tokens_as_zero(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    store.add_message(conversation_id, "user", "first", tokens_used=None)
    store.add_message(conversation_id, "assistant", "second", tokens_used=None)

    messages = store.get_recent_messages(conversation_id, token_budget=0)

    assert [m["content"] for m in messages] == ["first", "second"]


def test_get_recent_messages_never_exceeds_the_hard_history_cap(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    total_messages = _MAX_HISTORY_MESSAGES + 10
    for i in range(total_messages):
        store.add_message(conversation_id, "user", f"message {i}", tokens_used=0)

    messages = store.get_recent_messages(conversation_id, token_budget=10**9)

    assert len(messages) == _MAX_HISTORY_MESSAGES
    assert messages[0]["content"] == f"message {10}"
    assert messages[-1]["content"] == f"message {total_messages - 1}"


def test_get_recent_messages_does_not_load_content_for_discarded_history(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    conversation_id = store.create_conversation()
    store.add_message(conversation_id, "user", "old and discarded", tokens_used=1000)
    store.add_message(conversation_id, "user", "kept", tokens_used=10)

    messages = store.get_recent_messages(conversation_id, token_budget=50)

    assert [m["content"] for m in messages] == ["kept"]


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
