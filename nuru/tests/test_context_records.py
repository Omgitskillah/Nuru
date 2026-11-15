"""Persisted calibration is evidence to revalidate, never a blindly trusted slope."""
import json
import pytest
from core import context_records
from core.context_memory import Unsupported
from test_context_driver import setup


@pytest.mark.parametrize("change", [
    {"format": 2}, {"model": "another"}, {"fingerprint": "bad"}, {"placement": "unknown"},
    {"layout": {"architecture": "unknown", "window": 32768, "attention_layers": 8, "bytes_per_token": 32768}},
    {"samples": []}, {"samples": [{"window": True, "size": 1}] * 3},
])
def test_corrupt_or_future_calibration_is_preserved(tmp_path, change):
    d, r = setup(tmp_path); d.measure("qwen")
    path = context_records._path("qwen", d.root); data = json.loads(path.read_text()); data.update(change)
    path.write_text(json.dumps(data)); before = path.read_bytes()
    with pytest.raises(Unsupported): context_records.read("qwen", d.root)
    assert path.read_bytes() == before


def test_calibration_size_bound_and_symlink_refusal(tmp_path):
    d, r = setup(tmp_path); d.measure("qwen")
    path = context_records._path("qwen", d.root); path.write_bytes(b"x" * 8193)
    with pytest.raises(Unsupported): context_records.read("qwen", d.root)
    path.unlink(); target = tmp_path / "preserve"; target.write_text("unchanged")
    try: path.symlink_to(target)
    except OSError: pytest.skip("symlinks not available")
    with pytest.raises(Unsupported): context_records.read("qwen", d.root)
    assert target.read_text() == "unchanged"
