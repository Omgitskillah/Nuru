from core.modelfile import render_modelfile
import pytest

from core import session
from core.model_profile import ModelProfile


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
def no_real_model_build_or_sandbox(monkeypatch):
    """Session.start() would otherwise run a real `ollama create` (failing where Ollama isn't
    installed, as on CI) and ask the real Docker whether run_python can start."""
    monkeypatch.setattr(session, "create_model", lambda model, base_model, modelfile_path: render_modelfile(modelfile_path, base_model))
    monkeypatch.setattr(session, "python_server_params", lambda: None)


@pytest.fixture(autouse=True)
def no_background_indexer(monkeypatch):
    """Session.start() would otherwise start embedding the real vault through the real Ollama."""
    monkeypatch.setattr(session, "VaultIndexer", _NoIndexer)
    monkeypatch.setattr(session, "VaultLinkAuditor", _NoIndexer)


@pytest.fixture(autouse=True)
def neutral_model_profile(monkeypatch):
    """Whatever model the developer's .env names, a test sees a profile with no thinking map and no
    sampling of its own - so chat() gets exactly the arguments the test expects. Tests of the
    profiles set one themselves."""
    monkeypatch.setattr(session, "PROFILE", ModelProfile())


@pytest.fixture(autouse=True)
def isolate_conversation_files(monkeypatch, tmp_path):
    """Every Session test uses scratch history/logs, including executor tests."""
    monkeypatch.setattr(session, "LOG_DIR", tmp_path)
    monkeypatch.setattr(session, "DB_PATH", tmp_path / "conversation.db")
    monkeypatch.setenv("NURU_VAULTS_DIR", str(tmp_path / "vaults"))  # vault IDs and data: never ~/.nuru
    # A Session started without a vault serves VAULT_PATH, else .env.obsidian's: the developer's real
    # vault, which would be given a .nuru/vault-id. A test that wants one sets VAULT_PATH itself.
    (tmp_path / "test-vault").mkdir(exist_ok=True)
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "test-vault"))


@pytest.fixture(autouse=True)
def isolate_optional_steward(monkeypatch):
    monkeypatch.setattr(session.VaultSteward, 'from_env', lambda: None)
