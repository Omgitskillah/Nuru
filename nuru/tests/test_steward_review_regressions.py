"""Optional maintenance settings must not prevent chat startup."""

import pytest
from test_session import start_session
from core import session as session_module
from core.vault_steward import VaultSteward
from core.vault_link_auditor import VaultLinkAuditor


@pytest.mark.parametrize('worker,field', [(VaultSteward, 'steward'), (VaultLinkAuditor, 'link_auditor')])
def test_optional_config_error_disables_only_worker(monkeypatch, capsys, worker, field):
    def fail():
        raise ValueError('invalid optional settings')
    monkeypatch.setattr(session_module, worker.__name__, worker)
    monkeypatch.setattr(worker, 'from_env', fail)
    session = start_session(monkeypatch)
    assert getattr(session, field) is None
    assert 'disabled: invalid optional settings' in capsys.readouterr().err
    session.close()

