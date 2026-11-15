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
    def resident(self, model, *, exclusive=True):
        size = 4_800_000_000 + self.current * 37376 + self.size_offset
        return {"name": "nuru", "context_length": self.current, "size": size, "size_vram": size}
    def metadata(self, model):
        return {"size": 4_500_000_000, "model_info": {"general.architecture": "qwen35", "qwen35.context_length": 32768,
          "qwen35.block_count": 32, "qwen35.full_attention_interval": 4, "qwen35.attention.head_count_kv": 4,
          "qwen35.attention.key_length": 256, "qwen35.attention.value_length": 256}}, "digest", "0.40.11", self.fingerprint
    def verify(self, model, window, *, exclusive=True):
        from core.context_memory import Sample
        if window != self.current: raise Unsupported("wrong actual context")
        return Sample(window, self.resident(model, exclusive=exclusive)["size"]), "gpu"
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


def test_uncertain_apply_keeps_compute_ownership_and_blocks_future_work(tmp_path):
    from core import context_apply
    d, r = setup(tmp_path); d.measure("qwen")
    def timeout(model, window):
        r.uncertain = True
        raise Uncertain("load timed out")
    r.load = timeout
    with pytest.raises(context_apply.ApplyFailed): d.apply("qwen", 1, "initial")
    assert d.compute_blocked() and (tmp_path / "compute" / "local-model.lock").is_dir()
    assert prefs.read("qwen", d.root).revision == "initial"


def test_saved_choice_remains_unchanged_if_publication_fails_after_apply(tmp_path, monkeypatch):
    d, r = setup(tmp_path); d.measure("qwen")
    monkeypatch.setattr(prefs, "durable_write", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    result = d.apply("qwen", 1, "initial")
    assert "couldn't be saved" in result["notice"] and r.current == 26755
    assert prefs.read("qwen", d.root).revision == "initial" and d.window("qwen", {}, 8192) == 26755


def test_partial_gpu_placement_after_apply_is_explicit(tmp_path):
    d, r = setup(tmp_path); d.measure("qwen"); original = r.verify
    r.verify = lambda model, window, **kwargs: (original(model, window, **kwargs)[0], "mixed")
    result = d.apply("qwen", 1, "initial")
    assert "Doesn't fully fit in GPU memory" in result["notice"]


def test_saved_window_normal_chat_uses_nonexclusive_residency_checks(tmp_path):
    d, r = setup(tmp_path); d.measure("qwen"); d.apply("qwen", 1, "initial")
    original = r.resident
    def with_embedding(model, *, exclusive=True):
        if exclusive: raise Unsupported("Another model is resident")
        return original(model, exclusive=False)
    r.resident = with_embedding
    assert d.window("qwen", {}, 8192) == 26755
    d.verify_loaded("qwen")
    assert not d.describe("qwen")["can_measure"]
    with pytest.raises(Unsupported): d.apply("qwen", 1, prefs.read("qwen", d.root).revision)


def test_saved_choice_after_unload_uses_measured_curve_on_16gb_machine(tmp_path):
    d, r = setup(tmp_path); d.measure("qwen"); d.apply("qwen", 1, "initial")
    r.resident = lambda *args, **kwargs: None
    r.available = lambda: (16_000_000_000, 12_000_000_000)
    assert d.window("qwen", {}, 8192) == 26755


def test_exact_resident_choice_does_not_require_additional_free_memory(tmp_path):
    d, r = setup(tmp_path); d.measure("qwen"); d.apply("qwen", 1, "initial")
    def no_memory_probe():
        raise AssertionError("An existing verified allocation needs no memory preflight")
    r.available = no_memory_probe
    assert d.window("qwen", {}, 8192) == 26755
    r.size_offset = 500_000_000
    with pytest.raises(Unsupported): d.window("qwen", {}, 8192)


@pytest.mark.parametrize("initially_resident", [False, True])
def test_post_load_mismatch_cleans_only_newly_loaded_target(tmp_path, initially_resident):
    d, r = setup(tmp_path); d.measure("qwen"); d.apply("qwen", 1, "initial")
    original = r.resident
    if not initially_resident: r.resident = lambda *args, **kwargs: None
    d.before_load("qwen")
    r.resident = original
    r.size_offset = 500_000_000
    unloaded = []
    r.unload_owned = lambda model, **kwargs: unloaded.append((model, kwargs))
    with pytest.raises(Unsupported): d.verify_loaded("qwen")
    assert unloaded == ([] if initially_resident else [("qwen", {"exclusive": False})])
    assert not d.pending_loads


def test_status_poll_during_healthy_measurement_reports_busy_not_interrupted(tmp_path):
    d, r = setup(tmp_path); control = ContextSettings(d.service, d); original = r.cold_load; states = []
    def observed(model, window):
        info = control.recovery(); states.append(info)
        assert info["state"] == "busy" and info["own"] is True
        assert "restart" not in info["recovery"].lower()
        return original(model, window)
    r.cold_load = observed
    d.measure("qwen")
    assert states and control.recovery() == {"reserved": False}


def test_realistic_minimum_measure_then_smaller_apply_uses_existing_resident_evidence(tmp_path):
    d, r = setup(tmp_path); r.current = 32768
    original = r.resident
    r.resident = lambda model, **kwargs: {**original(model, **kwargs), "digest": "digest"}
    r.available = lambda: (17_180_000_000, 1_380_000_000)
    r.reclaimable = lambda row: row["size"] if row else 0
    d._minimum = lambda model: 20480
    info = d.measure("qwen")
    assert info["max_tokens"] == 32768 and r.calls == [8192, 16384, 32768]
    result = d.apply("qwen", 1, "initial")
    assert result["window_tokens"] == 26755 and prefs.read("qwen", d.root).tokens == 26755
    assert r.calls[-1] == 26755
    # Growth above that current allocation must still pass the memory budget.
    with pytest.raises(Unsupported, match="available memory"):
        d.apply("qwen", 2, prefs.read("qwen", d.root).revision)
    assert r.current == 26755 and prefs.read("qwen", d.root).gb == 1
