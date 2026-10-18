"""WP04 M2 and M3: a vault's conversations, service state and logs on this computer in its own folder,
never in the vault; and moving today's untagged data into one vault, in steps a crash can't leave
half done (core.vault_data)."""

import json
import os
import sqlite3

import pytest

from core import session, vault_data
from core.conversation_store import ConversationStore
from core.service.store import ServiceStore, _lock
from core.vault_identity import Registry


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    """Today's layout: one conversation in the Nuru folder's .db, a log in .logs."""
    db, logs = tmp_path / "nuru" / ".db", tmp_path / "nuru" / ".logs"
    db.mkdir(parents=True)
    logs.mkdir()
    (logs / "20261017-221131_d9463c5a.jsonl").write_text('{"event": "turn"}\n')
    conversations = ConversationStore(db / "conversation.db")
    store_id = conversations.create_conversation("Trip")
    conversations.add_message(store_id, "user", "Where am I going?")
    conversations.close()
    service = ServiceStore(db / "service.db")
    service.add_conversation("conv0000trip", "obsidian", store_id, "Trip")
    service.close()
    monkeypatch.setattr(vault_data, "LEGACY_DB", db)
    monkeypatch.setattr(vault_data, "LEGACY_LOGS", logs)
    return db


@pytest.fixture
def vault_uuid(tmp_path):
    folder = tmp_path / "NuruVault"
    folder.mkdir()
    return Registry().identify(folder)


def titles(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return [row[0] for row in conn.execute("SELECT title FROM conversations")]
    finally:
        conn.close()


def test_a_vaults_data_is_its_own_and_never_in_the_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DB_PATH", None)
    monkeypatch.setattr(session, "LOG_DIR", None)
    folder = tmp_path / "NuruVault"
    folder.mkdir()
    db_path, log_dir = session.data_paths(folder)
    vault_uuid = Registry().identify(folder)
    assert db_path == tmp_path / "vaults" / vault_uuid / "db" / "conversation.db"
    assert log_dir == tmp_path / "vaults" / vault_uuid / "logs"
    ConversationStore(db_path).close()
    other = tmp_path / "Other"
    other.mkdir()
    assert session.data_paths(other)[0] != db_path, "another vault, another history"
    assert not [p for p in folder.rglob("*") if p.suffix in {".db", ".sqlite3"}], "nothing of it in the vault"


def test_without_a_configured_vault_nothing_is_given_an_id(monkeypatch):
    monkeypatch.delenv("VAULT_PATH")
    monkeypatch.setattr(session, "ENV_DIR", session.ENV_DIR / "nowhere")
    with pytest.raises(ValueError, match="no vault is configured"):
        session.configured_vault()


def test_adopting_moves_the_old_history_and_keeps_the_old_files(legacy, vault_uuid, tmp_path):
    moved = vault_data.adopt_legacy(vault_uuid)
    live = tmp_path / "vaults" / vault_uuid / "db"
    assert moved["conversations"] == 1 and moved["logs"] == 1
    assert titles(live / "conversation.db") == ["Trip"] and titles(live / "service.db") == ["Trip"]
    assert (tmp_path / "vaults" / vault_uuid / "logs" / "20261017-221131_d9463c5a.jsonl").exists()
    assert not (legacy / "conversation.db").exists() and (legacy / "conversation.db.pre-wp04").exists()
    assert json.loads((tmp_path / "vaults" / vault_uuid / "migration.json").read_text())["state"] == "active"
    assert not vault_data.legacy_waiting()
    store = ServiceStore(live / "service.db")  # the moved state opens as the service would open it
    store.close()


def test_it_refuses_while_nuru_is_running(legacy, vault_uuid):
    holder = _lock(legacy / "service.db.lock")
    try:
        with pytest.raises(vault_data.MigrationError, match="Nuru is running"):
            vault_data.adopt_legacy(vault_uuid)
    finally:
        holder.close()
    assert (legacy / "conversation.db").exists(), "nothing changed"


def test_it_never_mixes_two_histories(legacy, vault_uuid, tmp_path):
    db, _ = vault_data.db_dir(vault_uuid), None
    conversations = ConversationStore(db / "conversation.db")
    conversations.create_conversation("Already here")
    conversations.close()
    with pytest.raises(vault_data.MigrationError, match="already holds conversations"):
        vault_data.adopt_legacy(vault_uuid)
    assert titles(db / "conversation.db") == ["Already here"] and (legacy / "conversation.db").exists()


def test_an_empty_history_made_before_the_move_is_replaced(legacy, vault_uuid):
    db = vault_data.db_dir(vault_uuid)
    ConversationStore(db / "conversation.db").close()
    ServiceStore(db / "service.db").close()
    vault_data.adopt_legacy(vault_uuid)
    assert titles(db / "conversation.db") == ["Trip"]


# --------------------------------------------------------------------------- a crash at each step
def crash_on(monkeypatch, when):
    """Make the step `when(name, args)` picks raise, as if the process died there."""
    real_replace, real_state = os.replace, vault_data._set_state

    def replace(src, dst):
        if when("replace", (str(src), str(dst))):
            raise KeyboardInterrupt("crashed")
        return real_replace(src, dst)

    def set_state(directory, **state):
        if when("state", state):
            raise KeyboardInterrupt("crashed")
        return real_state(directory, **state)

    monkeypatch.setattr(vault_data.os, "replace", replace)
    monkeypatch.setattr(vault_data, "_set_state", set_state)
    return lambda: (monkeypatch.setattr(vault_data.os, "replace", real_replace),
                    monkeypatch.setattr(vault_data, "_set_state", real_state))


def old_layout_works(legacy, vault_uuid, tmp_path):
    assert titles(legacy / "conversation.db") == ["Trip"] and titles(legacy / "service.db") == ["Trip"]
    live = tmp_path / "vaults" / vault_uuid / "db"
    assert not (live / "conversation.db").exists() or titles(live / "conversation.db") == []
    assert not (tmp_path / "vaults" / vault_uuid / "db.staging").exists()


def new_layout_is_complete(legacy, vault_uuid, tmp_path):
    live = tmp_path / "vaults" / vault_uuid / "db"
    assert titles(live / "conversation.db") == ["Trip"] and titles(live / "service.db") == ["Trip"]
    assert not (legacy / "conversation.db").exists() and not (legacy / "service.db").exists()
    assert json.loads((tmp_path / "vaults" / vault_uuid / "migration.json").read_text())["state"] == "active"


@pytest.mark.parametrize("point, outcome", [
    ("staged", "old"),             # before anything is recorded
    ("aside", "old"),              # an empty db/ moved aside, staging not yet in place
    ("activate", "old"),           # the one rename
    ("active", "new"),             # renamed, not yet recorded as active
    ("retire", "new"),             # active, an old file not yet retired
])
def test_a_crash_at_any_step_leaves_the_old_layout_or_the_new_one(legacy, vault_uuid, tmp_path, monkeypatch, point, outcome):
    db = vault_data.db_dir(vault_uuid)
    ConversationStore(db / "conversation.db").close()  # an empty history a new-layout service made
    picks = {
        "staged": lambda kind, x: kind == "state" and x.get("state") == "staged",
        "aside": lambda kind, x: kind == "replace" and x[1].endswith("db.replaced"),
        "activate": lambda kind, x: kind == "replace" and x[0].endswith("db.staging"),
        "active": lambda kind, x: kind == "state" and x.get("state") == "active",
        "retire": lambda kind, x: kind == "replace" and x[1].endswith("service.db.pre-wp04"),
    }
    restore = crash_on(monkeypatch, picks[point])
    with pytest.raises(KeyboardInterrupt):
        vault_data.adopt_legacy(vault_uuid)
    restore()
    vault_data.recover(vault_uuid)  # what the next start does before opening db/
    (old_layout_works if outcome == "old" else new_layout_is_complete)(legacy, vault_uuid, tmp_path)
    if outcome == "old":
        vault_data.adopt_legacy(vault_uuid)  # and the move can simply run again
        new_layout_is_complete(legacy, vault_uuid, tmp_path)
