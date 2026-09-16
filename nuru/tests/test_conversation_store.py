from core.conversation_store import ConversationStore


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
