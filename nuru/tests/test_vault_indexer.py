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
