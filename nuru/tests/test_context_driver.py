"""Production driver contract, persisted evidence and saved-window selection, no real model."""
from types import SimpleNamespace
from functools import partial

import pytest

from core.service.context_driver import ContextDriver
from core.service.context_settings import ContextSettings
from core.context_runner import Uncertain
from core.context_memory import Unsupported
from core.context_lease import ComputeLease
from core import context_preferences as prefs, context_records
from test_service_host import harness


class Runner:
    def __init__(self):
        self.uncertain = False; self.current = 8192; self.calls = []; self.size_offset = 0; self.fingerprint = "f" * 64
    def available(self): return 32_000_000_000, 28_000_000_000
    def reclaimable(self, row): return 0
    def resident(self, model):
        size = 4_800_000_000 + self.current * 37376 + self.size_offset
        return {"name": "nuru", "context_length": self.current, "size": size, "size_vram": size}
    def metadata(self, model):
        return {"size": 4_500_000_000, "model_info": {"general.architecture": "qwen35", "qwen35.context_length": 32768,
          "qwen35.block_count": 32, "qwen35.full_attention_interval": 4, "qwen35.attention.head_count_kv": 4,
          "qwen35.attention.key_length": 256, "qwen35.attention.value_length": 256}}, "digest", "0.40.11", self.fingerprint
    def verify(self, model, window):
        from core.context_memory import Sample
        if window != self.current: raise Unsupported("wrong actual context")
        return Sample(window, self.resident(model)["size"]), "gpu"
    def load(self, model, window):
        self.calls.append(window); self.current = window; return self.verify(model, window)
    def cold_load(self, model, window): return self.load(model, window)
    def unload_owned(self, model): raise AssertionError("Initially resident model must be restored")


def setup(tmp_path):
    session = SimpleNamespace(model_id=lambda: "qwen", _system_text=lambda: "Nuru system", tool_definitions=[])
    service = SimpleNamespace(_session=session, _model_changing=False)
    runner = Runner()
    driver = ContextDriver(service, runner, lambda info: min(info.get("context_window", 8192), 32768),
                           root=tmp_path / "preferences", lease=partial(ComputeLease, directory=tmp_path / "compute"))
    return driver, runner


def test_real_driver_measure_apply_persist_restart_and_saved_session_window(tmp_path):
    d, r = setup(tmp_path)
    before = d.describe("qwen"); assert before["can_measure"] and not before["measured"] and not r.calls
    result = d.measure("qwen")
    assert result["measured"] and result["needs_apply"] and result["max_tokens"] == 32768
    assert r.current == 8192 and r.calls == [8192, 16384, 32768, 8192]
    assert context_records.read("qwen", d.root).curve.maximum == 32768
    applied = d.apply("qwen", 1, "initial")
    assert applied["window_tokens"] == 26755 and not applied["needs_apply"]
    assert prefs.read("qwen", d.root).tokens == 26755
    assert d.window("qwen", {}, 8192) == 26755
    d.verify_loaded("qwen")
    restarted = ContextDriver(d.service, r, d.default_window, root=d.root, lease=d.lease)
    assert restarted.window("qwen", {}, 8192) == 26755
    restarted.verify_loaded("qwen")
    assert restarted.describe("qwen")["gb"] == 1


def test_missing_session_or_wrong_model_disables_measurement_with_plain_reason(tmp_path):
    d, r = setup(tmp_path); d.service._session = None
    result = d.describe("qwen"); assert not result["can_measure"] and "Send a message" in result["reason"]
    with pytest.raises(Unsupported): d.measure("qwen")
    assert r.calls == []


def test_changed_identity_and_memory_never_reuse_saved_window(tmp_path):
    d, r = setup(tmp_path); d.measure("qwen"); d.apply("qwen", 1, "initial")
    r.fingerprint = "a" * 64
    with pytest.raises(Unsupported): d.window("qwen", {}, 8192)
    with pytest.raises(Unsupported): d.apply("qwen", 1, prefs.read("qwen", d.root).revision)
    assert not d.describe("qwen")["measured"]
    r.fingerprint = "f" * 64; r.size_offset = 500_000_000
    with pytest.raises(Unsupported): d.verify_loaded("qwen")
    with pytest.raises(Unsupported): d.window("qwen", {}, 8192)


def test_admin_override_minimum_and_invalid_values(tmp_path, monkeypatch):
    d, r = setup(tmp_path)
    for value in ("bad", "0", "-1", "999999999"):
        monkeypatch.setenv("NURU_MAX_NUM_CTX", value)
        with pytest.raises(Unsupported): d.measure("qwen")
    monkeypatch.setenv("NURU_MAX_NUM_CTX", "16384")
    result = d.measure("qwen"); assert result["max_tokens"] == 16384 and "NURU_MAX" in result["limit_reason"]
    with pytest.raises(Unsupported): d.apply("qwen", 100, "initial")
    monkeypatch.setenv("NURU_MAX_NUM_CTX", "8192")
    with pytest.raises(Unsupported): d.measure("qwen")


def test_uncertainty_disables_control_and_runtime_use(tmp_path):
    d, r = setup(tmp_path); r.uncertain = True
    assert not d.describe("qwen")["can_measure"]
    with pytest.raises(Unsupported): d.window("qwen", {}, 8192)


def test_local_http_controller_drives_real_adapter_under_admission(harness, tmp_path):
    h = harness(); d, r = setup(tmp_path)
    h.service._session = d.service._session
    d.service = h.service
    h.service._models = lambda: {"default": "qwen", "choices": [{"id": "qwen", "cloud": False}]}
    controller = ContextSettings(h.service, d)
    info = controller.change({"model": "qwen", "operation": "measure", "expected_revision": "initial"})
    assert info["measured"] and not h.service._model_changing
    result = controller.change({"model": "qwen", "operation": "apply", "gb": 1, "expected_revision": "initial"})
    assert result["window_tokens"] == 26755
    h.service._session = None  # scripted harness owns its own close lifecycle
