import pytest

from core import session


class _NoIndexer:
    @staticmethod
    def from_env():
        return None


@pytest.fixture(autouse=True)
def decision_log_in_tmp(monkeypatch, tmp_path):
    """The decision layer's log goes to a temp file, never the real nuru/.db/decisions.sqlite3 - and
    every point is in shadow, so a test sees the session's own behaviour whatever the shipped config
    enforces. Tests of enforcement set their points themselves (test_enforcement.enforce_config)."""
    config = session.load_decision_config()
    shadow = {point: "shadow" for point in config.decision_points}
    monkeypatch.setattr(
        session, "load_decision_config",
        lambda: config.model_copy(update={"database": tmp_path / "decisions.sqlite3", "decision_points": shadow}),
    )


@pytest.fixture(autouse=True)
def no_real_expert_key(monkeypatch):
    """The real key lives in deep-think's .env; no test may reach it, or the API with it."""
    monkeypatch.setattr(session, "expert_api_key", lambda: None)


@pytest.fixture(autouse=True)
def no_background_indexer(monkeypatch):
    """Session.start() would otherwise start embedding the real vault through the real Ollama."""
    monkeypatch.setattr(session, "VaultIndexer", _NoIndexer)
