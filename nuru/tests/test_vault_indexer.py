import threading
import time

import pytest
from obsidian_mcp import semantic

from core import vault_indexer
from core.vault_indexer import VaultIndexer


def fake_embed(texts):
    return [[1.0, float(len(text) % 7), 0.5] for text in texts]


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.01)


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "a.md").write_text("First note.\n")
    (tmp_path / "b.md").write_text("Second note.\n")
    return tmp_path


def indexer_for(vault, embed=fake_embed, interval=30.0):
    return VaultIndexer(semantic.load_config(vault, {}), embed, interval=interval)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for name in ("VAULT_PATH", "OBSIDIAN_EMBED_MODEL", "OBSIDIAN_CHUNK_CHARS", "OBSIDIAN_SEMANTIC_DB", "OLLAMA_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(vault_indexer, "OBSIDIAN_ENV", tmp_path / "missing.env")


def test_from_env_is_none_without_a_vault(clean_env):
    assert VaultIndexer.from_env() is None


def test_from_env_reads_the_obsidian_servers_env_with_nurus_own_environment_winning(clean_env, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(f"VAULT_PATH={tmp_path}\nOBSIDIAN_CHUNK_CHARS=900\nOBSIDIAN_EMBED_MODEL=from-file\n")
    monkeypatch.setattr(vault_indexer, "OBSIDIAN_ENV", env_file)
    monkeypatch.setenv("OBSIDIAN_CHUNK_CHARS", "700")

    indexer = VaultIndexer.from_env()

    assert indexer.config.vault == tmp_path.resolve()
    assert (indexer.config.model, indexer.config.chunk_chars) == ("from-file", 700)


def test_run_once_embeds_changed_notes_and_counts_down_while_it_does(vault):
    seen = []

    def embed(texts):
        seen.append(indexer.pending)
        return fake_embed(texts)

    indexer = indexer_for(vault, embed)

    assert indexer.run_once().embedded == ["a.md", "b.md"]
    assert seen == [2, 1]
    assert indexer.pending == 0
    assert indexer.run_once().embedded == []


def test_stopping_ends_a_pass_between_notes(vault):
    def embed(texts):
        indexer.stop()
        return fake_embed(texts)

    indexer = indexer_for(vault, embed)

    assert indexer.run_once().embedded == ["a.md"]
    assert indexer.pending == 0


def test_active_thread_restart_reconciles_persisted_queue(vault):
    entered, release = threading.Event(), threading.Event()
    def blocking(texts):
        entered.set()
        assert release.wait(3)
        return fake_embed(texts)
    worker=indexer_for(vault,blocking)
    worker.start()
    assert entered.wait(3)
    worker._stop.set()  # Request stop while inference is active, then release it.
    release.set();worker.stop()
    assert not worker._thread.is_alive()
    assert worker.status['pending'] == ['b.md']
    resumed=indexer_for(vault)
    assert resumed.run_once().embedded == ['b.md']
    assert resumed.status['pending'] == []


def test_real_lock_is_observable_in_worker_and_recovers(vault):
    import sqlite3
    worker=indexer_for(vault,interval=.01)
    worker.run_once()
    locker=sqlite3.connect(worker.config.db_path)
    locker.execute('BEGIN IMMEDIATE')
    worker.start()
    try:
        wait_until(lambda:worker.error is not None,timeout=3)
        assert 'locked' in worker.error
        locker.rollback()
        wait_until(lambda:worker.error is None,timeout=3)
    finally:
        locker.close();worker.stop()


def test_the_thread_picks_up_notes_that_change_while_nuru_runs(vault):
    embedded = []

    def embed(texts):
        embedded.extend(texts)
        return fake_embed(texts)

    indexer = indexer_for(vault, embed, interval=0.02)
    indexer.start()
    try:
        wait_until(lambda: len(embedded) == 2)
        (vault / "c.md").write_text("A note written later.\n")
        wait_until(lambda: any("written later" in text for text in embedded))
    finally:
        indexer.stop()
    assert not indexer._thread.is_alive()


def test_a_failing_pass_is_reported_once_and_retried(vault, capsys):
    calls = threading.Event()
    attempts = []

    def embed(texts):
        attempts.append(texts)
        if len(attempts) >= 3:
            calls.set()
        raise ConnectionError("Can't reach Ollama")

    indexer = indexer_for(vault, embed, interval=0.01)
    indexer.start()
    try:
        assert calls.wait(5)
    finally:
        indexer.stop()

    assert capsys.readouterr().err.count("Can't reach Ollama") == 1
    assert indexer.error == "ConnectionError: Can't reach Ollama"
    assert indexer.pending == 0


def test_a_pass_that_changes_the_index_is_announced(vault, capsys):
    indexer = indexer_for(vault)
    seen = []
    indexer.on_change = seen.append

    indexer.run_once()

    assert (indexer.changes, indexer.last_change) == (1, "indexed 2 notes")
    assert seen[0].embedded == ["a.md", "b.md"]
    assert "Nuru: semantic index - indexed 2 notes: a.md, b.md" in capsys.readouterr().err


def test_a_pass_that_changes_nothing_is_not_announced(vault, capsys):
    indexer = indexer_for(vault)
    indexer.run_once()
    capsys.readouterr()

    indexer.run_once()

    assert indexer.changes == 1
    assert capsys.readouterr().err == ""


def test_the_announcement_names_moves_and_removals_and_caps_a_long_list(tmp_path, capsys):
    for n in range(7):
        (tmp_path / f"n{n}.md").write_text(f"Note {n}.\n")
    indexer = indexer_for(tmp_path)
    indexer.run_once()
    capsys.readouterr()
    (tmp_path / "sub").mkdir()
    (tmp_path / "n0.md").rename(tmp_path / "sub" / "n0.md")
    (tmp_path / "n1.md").unlink()

    indexer.run_once()

    assert indexer.last_change == "moved 1, removed 1"
    assert "n0.md -> sub/n0.md, n1.md (removed)" in capsys.readouterr().err
    assert vault_indexer.describe(semantic.RefreshResult(embedded=["x"] * 7)) == "indexed 7 notes"
    assert vault_indexer._paths_line(semantic.RefreshResult(embedded=[f"p{n}" for n in range(7)])).endswith(
        "p4 and 2 more"
    )
