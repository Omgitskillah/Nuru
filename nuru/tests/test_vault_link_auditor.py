import json
import sys
import socket
import threading

import pytest

from core import vault_link_auditor
from core.vault_link_auditor import VaultLinkAuditor
from obsidian_mcp.link_integrity import AuditCancelled


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('Link bookkeeping must not call models or networks')
    monkeypatch.setattr(socket, 'create_connection', fail)


def test_opt_in_env_and_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(vault_link_auditor, 'ENV_DIR', tmp_path)
    for key in ['NURU_LINK_AUDIT', 'NURU_LINK_AUDIT_INTERVAL', 'VAULT_PATH', 'OBSIDIAN_LINK_POLICY']:
        monkeypatch.delenv(key, raising=False)
    (tmp_path / '.env.obsidian').write_text(f'VAULT_PATH={tmp_path}\n')
    assert VaultLinkAuditor.from_env() is None
    (tmp_path / '.env.nuru').write_text('NURU_LINK_AUDIT=1\nNURU_LINK_AUDIT_INTERVAL=40\n')
    worker = VaultLinkAuditor.from_env()
    assert worker.interval == 40
    monkeypatch.setenv('NURU_LINK_AUDIT', '0')
    assert VaultLinkAuditor.from_env() is None
    monkeypatch.setenv('NURU_LINK_AUDIT', '1')
    monkeypatch.setenv('NURU_LINK_AUDIT_INTERVAL', '0')
    with pytest.raises(ValueError):
        VaultLinkAuditor.from_env()


def test_changed_snapshots_only_and_coalesced_edits(tmp_path):
    source = tmp_path / 'Source.md'
    source.write_text('[[Missing]]')
    worker = VaultLinkAuditor(tmp_path)
    seen = []
    worker.on_change = seen.append
    first = worker.run_once()
    worker.run_once()
    assert worker.changes == 1
    for i in range(100):
        source.write_text(f'[[Missing{i}]]')
    final = worker.run_once()
    assert worker.changes == 2
    assert len(seen) == 2
    assert final['references'][0]['target'] == 'Missing99'
    assert first['references'][0]['target'] == 'Missing'
    assert final['stats']['model_calls'] == 0


def test_policy_reloaded_without_note_edit(tmp_path):
    (tmp_path / 'Source.md').write_text('[[Future]]')
    policy = tmp_path / 'policy.json'
    policy.write_text('{}')
    worker = VaultLinkAuditor(tmp_path, policy_path=policy)
    assert worker.run_once()['counts']['broken'] == 1
    policy.write_text(json.dumps({'intentional': [{'source': 'Source.md', 'target': 'Future'}]}))
    report = worker.run_once()
    assert report['counts']['intentional-unwritten'] == 1
    assert report['stats']['parsed_notes'] == 0


def test_background_polling_recovers_missed_changes_and_stops(tmp_path):
    note = tmp_path / 'Source.md'
    note.write_text('[[Missing]]')
    worker = VaultLinkAuditor(tmp_path, interval=0.01)
    first, resolved = threading.Event(), threading.Event()
    def changed(report):
        first.set()
        if report['counts']['resolved'] == 1:
            resolved.set()
    worker.on_change = changed
    worker.start()
    try:
        assert first.wait(3)
        (tmp_path / 'Missing.md').write_text('# Created outside Nuru')
        assert resolved.wait(3)
    finally:
        worker.stop()
    assert not worker._thread.is_alive()


def test_stopping_cancels_between_files_without_partial_publish(tmp_path):
    (tmp_path / 'Source.md').write_text('[[Missing]]')
    worker = VaultLinkAuditor(tmp_path)
    worker.stop()
    with pytest.raises(AuditCancelled):
        worker.run_once()
    assert worker.latest is None


def test_background_failure_is_retried_and_logged_once(tmp_path, monkeypatch, capsys):
    worker = VaultLinkAuditor(tmp_path, interval=0.01)
    retried = threading.Event()
    attempts = []
    original = worker.run_once
    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError('temporary denial')
        report = original()
        retried.set()
        return report
    monkeypatch.setattr(worker, 'run_once', flaky)
    worker.start()
    try:
        assert retried.wait(3)
    finally:
        worker.stop()
    assert capsys.readouterr().err.count('temporary denial') == 1
    assert worker.error is None


def test_standalone_nuru_command_needs_no_model(tmp_path, monkeypatch, capsys):
    (tmp_path / 'Source.md').write_text('[[Missing]]')
    monkeypatch.setattr('sys.argv', ['nuru-vault-links', str(tmp_path)])
    vault_link_auditor.main()
    assert json.loads(capsys.readouterr().out)['counts']['broken'] == 1


@pytest.mark.parametrize('interval', [0, -1, float('nan'), float('inf')])
def test_invalid_intervals_are_rejected(tmp_path, interval):
    with pytest.raises(ValueError):
        VaultLinkAuditor(tmp_path, interval=interval)


def test_a_scan_cancelled_by_stop_ends_the_background_worker_quietly(tmp_path, monkeypatch, capsys):
    worker = VaultLinkAuditor(tmp_path, interval=0.01)
    def cancelled():
        worker._stop.set()
        raise AuditCancelled()
    monkeypatch.setattr(worker, 'run_once', cancelled)
    worker.start()
    worker._thread.join(3)
    assert not worker._thread.is_alive() and worker.error is None
    assert capsys.readouterr().err == ''


class Watched(VaultLinkAuditor):
    """The CLI's worker, kept so the test can end the watch the way a user would."""
    made = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        Watched.made.append(self)


def test_watch_mode_prints_each_changed_report_until_stopped(tmp_path, monkeypatch, capsys):
    (tmp_path / 'Source.md').write_text('[[Missing]]')
    Watched.made = []
    monkeypatch.setattr(vault_link_auditor, 'VaultLinkAuditor', Watched)
    monkeypatch.setattr('sys.argv', ['nuru-vault-links', str(tmp_path), '--watch', '--interval', '0.01'])
    cli = threading.Thread(target=vault_link_auditor.main)
    cli.start()
    deadline = threading.Event()
    while not (Watched.made and Watched.made[0].changes) and not deadline.wait(0.01):
        pass
    Watched.made[0].stop()
    cli.join(3)
    assert not cli.is_alive()
    (line,) = capsys.readouterr().out.splitlines()  # one changed report; unchanged passes print nothing
    assert json.loads(line)['counts']['broken'] == 1


def test_ctrl_c_in_watch_mode_stops_the_worker(tmp_path, monkeypatch, capsys):
    class Interrupted(VaultLinkAuditor):
        def start(self):
            super().start()
            join = self._thread.join
            calls = []
            def interrupt_once(timeout=None):
                calls.append(timeout)
                if len(calls) == 1:
                    raise KeyboardInterrupt
                return join(timeout)
            self._thread.join = interrupt_once
            Watched.made = [self]
    monkeypatch.setattr(vault_link_auditor, 'VaultLinkAuditor', Interrupted)
    monkeypatch.setattr('sys.argv', ['nuru-vault-links', str(tmp_path), '--watch', '--interval', '0.01'])
    vault_link_auditor.main()
    assert not Watched.made[0]._thread.is_alive()


def test_the_module_runs_as_a_command(tmp_path, monkeypatch, capsys):
    import runpy
    (tmp_path / 'Source.md').write_text('[[Missing]]')
    monkeypatch.setattr('sys.argv', ['vault_link_auditor', str(tmp_path)])
    monkeypatch.delitem(sys.modules, 'core.vault_link_auditor')  # run as a fresh __main__, as `python -m` would
    runpy.run_module('core.vault_link_auditor', run_name='__main__')
    assert json.loads(capsys.readouterr().out)['counts']['broken'] == 1
