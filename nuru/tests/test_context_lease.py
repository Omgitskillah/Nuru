"""Compute leases never expire, steal ownership or recursively delete shared files."""
import json
import pytest
from core.context_lease import ComputeLease
from core.context_memory import Unsupported


def test_normal_lease_releases_only_its_owned_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("NURU_SHARED_COMPUTE_DIR", str(tmp_path))
    with ComputeLease("qwen") as lease:
        data = json.loads((lease.path / "owner.json").read_text())
        assert data["run_id"] == lease.run and data["models"] == ["qwen"]
        with pytest.raises(Unsupported):
            with ComputeLease("another", tmp_path): pass
    assert not lease.path.exists()


def test_uncertain_lease_preserves_owner_and_directory(tmp_path):
    with ComputeLease("qwen", tmp_path) as lease: lease.retain("load timed out")
    assert lease.path.is_dir() and json.loads((lease.path / "owner.json").read_text())["uncertain"] == "load timed out"


@pytest.mark.parametrize("contents", ["broken", '{"run_id":"another"}'])
def test_missing_or_changed_owner_is_never_deleted(tmp_path, contents):
    with pytest.raises(Unsupported):
        with ComputeLease("qwen", tmp_path) as lease:
            (lease.path / "owner.json").write_text(contents)
    assert lease.path.exists()


def test_compute_directory_symlink_cannot_redirect_ownership(tmp_path):
    target = tmp_path / "target"; target.mkdir(); link = tmp_path / "link"
    try: link.symlink_to(target, target_is_directory=True)
    except OSError: pytest.skip("symlinks not available")
    with pytest.raises(Unsupported):
        with ComputeLease("qwen", link): pass
    assert list(target.iterdir()) == []
