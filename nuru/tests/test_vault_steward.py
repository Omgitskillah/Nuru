import json
import pytest

from core.vault_steward import VaultSteward
from obsidian_mcp.link_integrity import AuditCancelled


def fixture(tmp_path):
    vault = tmp_path / 'vault'
    vault.mkdir()
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'packages': ['properties'], 'schemas': [{'name': 'n', 'fields': {'owner': {'type': 'string', 'required': True}}}]}))
    return vault, config


def test_worker_coalesces_config_recheck_and_delete(tmp_path):
    vault, config = fixture(tmp_path)
    (vault / 'a.md').write_text('text')
    worker = VaultSteward(vault, config, state_path=vault / '.state.sqlite3')
    reports = []
    worker.on_change = reports.append
    first = worker.run_once()
    assert first['finding_count'] == 1 and first['model_calls'] == 0
    worker.run_once()
    assert worker.changes == 1
    (vault / 'a.md').write_text('change 1')
    (vault / 'a.md').write_text('change 2')
    worker.run_once()
    assert worker.changes == 2
    config.write_text('{"packages": []}')
    assert worker.run_once()['finding_count'] == 0
    assert worker.changes == 3
    assert len(reports) == 3


def test_disabled_and_invalid_interval(monkeypatch, tmp_path):
    monkeypatch.setenv('NURU_STEWARD_ENABLED', '0')
    assert VaultSteward.from_env() is None
    vault, config = fixture(tmp_path)
    for interval in [0, .5, float('nan')]:
        with pytest.raises(ValueError):
            VaultSteward(vault, config, interval=interval)


def test_stopped_scan_never_publishes(tmp_path):
    vault, config = fixture(tmp_path)
    (vault / 'a.md').write_text('text')
    worker = VaultSteward(vault, config)
    worker.stop()
    with pytest.raises(AuditCancelled):
        worker.run_once()
    assert worker.latest is None


def test_bad_config_leaves_previous_report(tmp_path):
    vault, config = fixture(tmp_path)
    worker = VaultSteward(vault, config)
    first = worker.run_once()
    config.write_text('{invalid')
    with pytest.raises(ValueError):
        worker.run_once()
    assert worker.latest is first
