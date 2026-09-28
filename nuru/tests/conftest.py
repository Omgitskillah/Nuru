import pytest

from core import session


class _NoIndexer:
    @staticmethod
    def from_env():
        return None


@pytest.fixture(autouse=True)
def decision_log_in_tmp(monkeypatch, tmp_path):
    """The decision layer's log goes to a temp file, never the real nuru/.db/decisions.sqlite3."""
    config = session.load_decision_config()
    monkeypatch.setattr(
        session, "load_decision_config", lambda: config.model_copy(update={"database": tmp_path / "decisions.sqlite3"})
    )


@pytest.fixture(autouse=True)
def no_background_indexer(monkeypatch):
    """Session.start() would otherwise start embedding the real vault through the real Ollama."""
    monkeypatch.setattr(session, "VaultIndexer", _NoIndexer)
