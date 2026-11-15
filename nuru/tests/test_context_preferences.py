"""F40 choice storage preserves data, rejects stale editors, and never loads a model."""
import json
import os
import stat
from dataclasses import replace, asdict

import pytest
from core import context_preferences as prefs, user_models
from core.context_memory import Identity, Geometry, Unsupported

MODEL = "library/qwen3.5:latest"
IDENTITY = Identity("sha256:model", "0.99.0", "f16")
LAYOUT = Geometry("qwen35", 131072, 8, 32768)


def save(root, expected=prefs.INITIAL, model=MODEL, **changes):
    args = dict(gb=1, tokens=30517, identity=IDENTITY, layout=LAYOUT, root=root)
    args.update(changes)
    return prefs.publish(model, expected, **args)


def test_per_model_choices_survive_restart_and_are_private(tmp_path, monkeypatch):
    root = tmp_path / "contexts"
    monkeypatch.setenv("NURU_MODELS_DIR", str(tmp_path / "models"))
    assert prefs.directory() == root.with_name("context-memory")
    assert prefs.read(MODEL, root) == prefs.Choice(MODEL)
    assert not root.exists()
    first = save(root)
    assert prefs.read(MODEL, root) == first
    assert first.usable(IDENTITY, LAYOUT)
    assert not first.usable(replace(IDENTITY, digest="new"), LAYOUT)
    assert not first.usable(IDENTITY, replace(LAYOUT, window=262144))
    other = save(root, model="library/qwen3.5:other", gb=2)
    assert other.gb == 2 and prefs.read(MODEL, root) == first
    assert all("qwen" not in p.name for p in root.iterdir())
    if os.name != "nt":
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(prefs._path(MODEL, root).stat().st_mode) == 0o600
    default = prefs.publish(MODEL, prefs.INITIAL, gb=None, tokens=None, identity=None, layout=None)
    assert prefs.read(MODEL) == default


def test_revision_conflicts_and_reset_do_not_reuse_old_revision(tmp_path):
    first = save(tmp_path)
    with pytest.raises(prefs.Conflict): save(tmp_path)
    reset = save(tmp_path, first.revision, gb=None, tokens=None, identity=None, layout=None)
    assert reset.revision != first.revision and not reset.usable(IDENTITY, LAYOUT)
    assert prefs.read(MODEL, tmp_path) == reset
    again = save(tmp_path, reset.revision)
    with pytest.raises(prefs.Conflict): save(tmp_path, first.revision)
    assert prefs.read(MODEL, tmp_path) == again


@pytest.mark.parametrize("contents", [b"{", b"\xff", b"[]", b"x" * (prefs.MAX_BYTES + 1)])
def test_unreadable_settings_never_overwritten(tmp_path, contents):
    path = prefs._path(MODEL, tmp_path); path.write_bytes(contents)
    with pytest.raises(Unsupported): prefs.read(MODEL, tmp_path)
    with pytest.raises(Unsupported): save(tmp_path)
    assert path.read_bytes() == contents


@pytest.mark.parametrize("changes", [
    {"format": 2}, {"format": True}, {"model": "another"}, {"revision": "bad"},
    {"extra": "unknown"}, {"gb": True}, {"tokens": 0}, {"tokens": 262144}, {"tokens": 131072},
    {"identity": None}, {"identity": {**asdict(IDENTITY), "kv_type": "q8_0"}},
    {"layout": {**asdict(LAYOUT), "architecture": "unknown"}},
    {"layout": {**asdict(LAYOUT), "attention_layers": 0}},
    {"layout": {**asdict(LAYOUT), "bytes_per_token": -1}},
    {"layout": {**asdict(LAYOUT), "window": True}},
    {"gb": None},
])
def test_corrupt_or_future_settings_preserved(tmp_path, changes):
    save(tmp_path); path = prefs._path(MODEL, tmp_path)
    data = json.loads(path.read_text()); data.update(changes)
    path.write_text(json.dumps(data)); before = path.read_bytes()
    with pytest.raises(Unsupported): prefs.read(MODEL, tmp_path)
    with pytest.raises(Unsupported): save(tmp_path)
    assert path.read_bytes() == before


def test_failed_atomic_publication_keeps_previous_choice_and_no_temporary_file(tmp_path, monkeypatch):
    first = save(tmp_path); path = prefs._path(MODEL, tmp_path); before = path.read_bytes()
    def fail(*args): raise OSError("disk full")
    monkeypatch.setattr(user_models.os, "replace", fail)
    with pytest.raises(OSError): save(tmp_path, first.revision, gb=2)
    assert path.read_bytes() == before and not list(tmp_path.glob("*.tmp"))


def test_bad_model_names_and_symlinks_refused(tmp_path):
    with pytest.raises(user_models.ConfigError): prefs.read("../../other", tmp_path)
    target = tmp_path / "target"; target.mkdir(); link = tmp_path / "link"
    try: link.symlink_to(target, target_is_directory=True)
    except OSError: pytest.skip("symlinks not available")
    with pytest.raises(Unsupported): prefs.read(MODEL, link)
    path = prefs._path(MODEL, tmp_path); other = tmp_path / "other"; other.write_text("keep")
    path.symlink_to(other)
    with pytest.raises(Unsupported): prefs.read(MODEL, tmp_path)
    assert other.read_text() == "keep"
