"""Recovery requires a dead owner, exact identity and explicit server restart acknowledgement."""
import json
from types import SimpleNamespace

import psutil
import pytest

from core import context_recovery as recovery
from core.context_lease import ComputeLease
from core.context_memory import Unsupported


def abandoned(tmp_path, monkeypatch):
    with ComputeLease("qwen", tmp_path) as lease: lease.retain("uncertain")
    def dead(pid): raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(recovery.psutil, "Process", dead)
    return lease


def test_no_reservation_is_read_only(tmp_path):
    before = set(tmp_path.iterdir())
    assert recovery.inspect(tmp_path) == {"reserved": False}
    assert set(tmp_path.iterdir()) == before


def test_live_owner_cannot_be_released_even_with_acknowledgement(tmp_path):
    with ComputeLease("qwen", tmp_path) as lease:
        assert recovery.inspect(tmp_path)["owner_running"]
        with pytest.raises(Unsupported, match="still running"):
            recovery.release(lease.run, ollama_restarted=True, directory=tmp_path)
        assert lease.path.exists()


def test_dead_owner_requires_restart_confirmation_and_exact_run(tmp_path, monkeypatch):
    lease = abandoned(tmp_path, monkeypatch)
    assert recovery.inspect(tmp_path)["owner_running"] is False
    for expected, confirmed in ((lease.run, False), ("different", True)):
        with pytest.raises(Unsupported): recovery.release(expected, ollama_restarted=confirmed, directory=tmp_path)
        assert lease.path.exists()
    assert recovery.release(lease.run, ollama_restarted=True, directory=tmp_path) == {"released": lease.run}
    assert not lease.path.exists()


def test_extra_files_are_preserved(tmp_path, monkeypatch):
    lease = abandoned(tmp_path, monkeypatch); (lease.path / "other").write_text("keep")
    with pytest.raises(Unsupported, match="additional"):
        recovery.release(lease.run, ollama_restarted=True, directory=tmp_path)
    assert (lease.path / "other").read_text() == "keep"


def test_pid_reuse_is_not_the_old_owner(tmp_path, monkeypatch):
    with ComputeLease("qwen", tmp_path) as lease: lease.retain("uncertain")
    monkeypatch.setattr(recovery.psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: 1.0))
    assert recovery.inspect(tmp_path)["owner_running"] is False


def test_access_denied_never_means_owner_dead(tmp_path, monkeypatch):
    lease = abandoned(tmp_path, monkeypatch)
    def denied(pid): raise psutil.AccessDenied(pid)
    monkeypatch.setattr(recovery.psutil, "Process", denied)
    with pytest.raises(Unsupported, match="Cannot check"):
        recovery.release(lease.run, ollama_restarted=True, directory=tmp_path)
    assert lease.path.exists()


@pytest.mark.parametrize("changes", [{"owner": "Claude"}, {"pid": True}, {"process_created": -1}, {"run_id": "bad"}])
def test_unknown_or_incomplete_metadata_is_preserved(tmp_path, monkeypatch, changes):
    lease = abandoned(tmp_path, monkeypatch); path = lease.path / "owner.json"
    data = json.loads(path.read_text()); data.update(changes); path.write_text(json.dumps(data))
    with pytest.raises(Unsupported): recovery.inspect(tmp_path)
    assert path.exists()


def test_changed_snapshot_cannot_release_replacement(tmp_path, monkeypatch):
    lease = abandoned(tmp_path, monkeypatch); original = recovery._snapshot; calls = []
    def changed(directory):
        value = original(directory); calls.append(1)
        if len(calls) == 2: return (*value[:3], (0, 0, b"replacement"))
        return value
    monkeypatch.setattr(recovery, "_snapshot", changed)
    with pytest.raises(Unsupported, match="changed during"):
        recovery.release(lease.run, ollama_restarted=True, directory=tmp_path)
    assert lease.path.exists()


def test_legacy_pid_that_exists_is_conservatively_live(tmp_path, monkeypatch):
    with ComputeLease("qwen", tmp_path) as lease: lease.retain("uncertain")
    path = lease.path / "owner.json"; data = json.loads(path.read_text()); del data["process_created"]
    path.write_text(json.dumps(data))
    assert recovery.inspect(tmp_path)["owner_running"]


def test_symlink_metadata_and_foreign_paths_are_not_followed(tmp_path, monkeypatch):
    lease = abandoned(tmp_path, monkeypatch); path = lease.path / "owner.json"
    target = tmp_path / "keep"; target.write_text(path.read_text()); path.unlink()
    try: path.symlink_to(target)
    except OSError: pytest.skip("symlinks unavailable")
    with pytest.raises(Unsupported, match="unsafe"): recovery.inspect(tmp_path)
    assert target.exists()
    alias = tmp_path / "alias"; alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(Unsupported, match="symbolic"): recovery.inspect(alias)


def test_absent_release_and_oversize_metadata_preserved(tmp_path, monkeypatch):
    with pytest.raises(Unsupported): recovery.release("x", ollama_restarted=True, directory=tmp_path)
    lease = abandoned(tmp_path, monkeypatch); path = lease.path / "owner.json"; path.write_text("x" * 8193)
    with pytest.raises(Unsupported): recovery.inspect(tmp_path)
    assert path.stat().st_size == 8193


def test_cli_check_and_explicit_release(tmp_path, monkeypatch, capsys):
    from core.service import server
    monkeypatch.setenv("NURU_SHARED_COMPUTE_DIR", str(tmp_path))
    server.main(["compute", "check"])
    assert json.loads(capsys.readouterr().out) == {"reserved": False}
    lease = abandoned(tmp_path, monkeypatch)
    with pytest.raises(SystemExit): server.main(["compute", "release", "--run-id", lease.run])
    assert lease.path.exists()
    capsys.readouterr()
    server.main(["compute", "release", "--run-id", lease.run, "--ollama-restarted"])
    assert json.loads(capsys.readouterr().out) == {"released": lease.run}


@pytest.mark.parametrize("delta", [-1.0, -0.5, 0.0, 0.5, 1.0])
def test_small_creation_time_difference_preserves_live_owner(tmp_path, monkeypatch, delta):
    with ComputeLease("qwen", tmp_path) as lease: lease.retain("uncertain")
    created = json.loads((lease.path / "owner.json").read_text())["process_created"]
    monkeypatch.setattr(recovery.psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: created + delta))
    assert recovery.inspect(tmp_path)["owner_running"] is True
    with pytest.raises(Unsupported, match="still running"):
        recovery.release(lease.run, ollama_restarted=True, directory=tmp_path)
    assert lease.path.exists()
