import json
import sys
import time
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


def test_from_env_needs_the_vault_and_config_and_reads_the_rest(tmp_path, monkeypatch):
    """conftest stubs VaultSteward.from_env for every test, so this loads its own copy of the module."""
    import importlib.util
    from core import vault_steward as installed
    spec = importlib.util.spec_from_file_location('steward_under_test', installed.__file__)
    vault_steward = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vault_steward)
    VaultSteward = vault_steward.VaultSteward
    vault, config = fixture(tmp_path)
    monkeypatch.setattr(vault_steward, 'ENV_DIR', tmp_path)  # no .env files there
    for key in ['VAULT_PATH', 'OBSIDIAN_STEWARD_CONFIG', 'NURU_STEWARD_STATE', 'NURU_STEWARD_INTERVAL']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('NURU_STEWARD_ENABLED', '0')
    assert VaultSteward.from_env() is None
    monkeypatch.setenv('NURU_STEWARD_ENABLED', '1')
    with pytest.raises(ValueError, match='requires VAULT_PATH and OBSIDIAN_STEWARD_CONFIG'):
        VaultSteward.from_env()
    monkeypatch.setenv('VAULT_PATH', str(vault))
    monkeypatch.setenv('OBSIDIAN_STEWARD_CONFIG', str(config))
    worker = VaultSteward.from_env()
    assert (worker.vault, worker.config_path, worker.state_path, worker.interval) == (vault, config, None, 30)
    monkeypatch.setenv('NURU_STEWARD_STATE', str(tmp_path / 'state.sqlite3'))
    monkeypatch.setenv('NURU_STEWARD_INTERVAL', '45')
    worker = VaultSteward.from_env()
    assert worker.state_path == tmp_path / 'state.sqlite3' and worker.interval == 45


def test_background_failure_is_logged_once_and_a_later_pass_recovers(tmp_path, monkeypatch, capsys):
    import threading
    vault, config = fixture(tmp_path)
    worker = VaultSteward(vault, config, interval=1)
    recovered, attempts = threading.Event(), []
    original = worker.run_once
    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError('temporary denial')
        report = original()
        recovered.set()
        return report
    monkeypatch.setattr(worker, 'run_once', flaky)
    monkeypatch.setattr(worker._stop, 'wait', lambda seconds: worker._stop.is_set())  # no 1 s waits between passes
    worker.start()
    try:
        assert recovered.wait(5)
    finally:
        worker._stop.set()
        worker._thread.join(5)
    assert not worker._thread.is_alive()
    assert capsys.readouterr().err.count('Nuru: stewardship unavailable: PermissionError: temporary denial') == 1
    assert worker.error is None


def test_a_pass_cancelled_by_stop_ends_the_background_worker_quietly(tmp_path, monkeypatch, capsys):
    vault, config = fixture(tmp_path)
    worker = VaultSteward(vault, config)
    def cancelled():
        worker._stop.set()
        raise AuditCancelled()
    monkeypatch.setattr(worker, 'run_once', cancelled)
    worker.start()
    worker._thread.join(3)
    assert not worker._thread.is_alive() and worker.error is None
    worker.stop()  # already stopped: returns at once
    assert capsys.readouterr().err == ''


def test_the_command_prints_one_report_and_runs_as_a_module(tmp_path, monkeypatch, capsys):
    import runpy
    vault, config = fixture(tmp_path)
    (vault / 'a.md').write_text('text')
    monkeypatch.setattr('sys.argv', ['nuru-steward', str(vault), '--config', str(config)])
    monkeypatch.delitem(sys.modules, 'core.vault_steward')  # run as a fresh __main__, as `python -m` would
    runpy.run_module('core.vault_steward', run_name='__main__')
    report = json.loads(capsys.readouterr().out)
    assert report['finding_count'] == 1 and report['model_calls'] == 0


def test_watch_mode_prints_changed_reports_until_ctrl_c(tmp_path, monkeypatch, capsys):
    from core import vault_steward
    vault, config = fixture(tmp_path)
    (vault / 'a.md').write_text('text')
    made = []

    class Watched(VaultSteward):
        def start(self):
            made.append(self)
            super().start()
            join, deadline = self._thread.join, time.monotonic() + 10
            def interrupt_after_a_report(timeout=None):
                # Ctrl-C once a report is out, or after 10 s without one, so a regression fails, not hangs.
                if (self.changes or time.monotonic() > deadline) and not getattr(self, 'interrupted', False):
                    self.interrupted = True
                    raise KeyboardInterrupt
                return join(timeout)
            self._thread.join = interrupt_after_a_report
    monkeypatch.setattr(vault_steward, 'VaultSteward', Watched)
    monkeypatch.setattr('sys.argv', ['nuru-steward', str(vault), '--config', str(config), '--watch', '--interval', '1'])
    vault_steward.main()
    assert not made[0]._thread.is_alive()
    (line,) = capsys.readouterr().out.splitlines()
    assert json.loads(line)['finding_count'] == 1
