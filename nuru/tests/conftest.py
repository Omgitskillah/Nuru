import pytest

from core import session


class _NoIndexer:
    @staticmethod
    def from_env():
        return None


@pytest.fixture(autouse=True)
def no_background_indexer(monkeypatch):
    """Session.start() would otherwise start embedding the real vault through the real Ollama."""
    monkeypatch.setattr(session, "VaultIndexer", _NoIndexer)
