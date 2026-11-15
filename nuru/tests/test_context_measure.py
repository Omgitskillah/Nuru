"""Bounded orchestration, fault injection and mandatory restore; no model loads."""
from types import SimpleNamespace
import pytest
from core.context_measure import measure
from core.context_memory import Geometry, Sample, Unsupported
from core.context_runner import Uncertain

LAYOUT = Geometry("qwen35", 262144, 8, 32768)


class Fake:
    def __init__(self, previous=32768):
        self.previous = previous; self.uncertain = False; self.calls = []; self.fault = None
        self.changed_identity = False; self.bad_candidates = False; self.restore_error = False
    def available(self): return 32_000_000_000, 28_000_000_000
    def reclaimable(self, row): return 0
    def resident(self, model):
        return None if self.previous is None else {"context_length": self.previous, "size": 4_800_000_000 + self.previous * 37376, "size_vram": 4_800_000_000 + self.previous * 37376}
    def metadata(self, model): return {"size": 4_500_000_000}, "digest", "version", "changed" if self.changed_identity else "key"
    def cold_load(self, model, window):
        self.calls.append(window)
        if self.fault == len(self.calls):
            self.uncertain = True; raise Uncertain("timeout")
        if self.restore_error and len(self.calls) > 2 and window == self.previous: raise OSError("restore")
        if self.bad_candidates and len(self.calls) in (3, 4): return Sample(window, 40_000_000_000), "gpu"
        return Sample(window, 4_800_000_000 + window * 37376), "gpu"
    def unload_owned(self, model): self.calls.append("unload")


class Lease:
    def __init__(self): self.retained = False
    def retain(self, reason): self.retained = True


def test_bounded_candidates_restore_previous_and_progress_excludes_restore():
    r, lease, steps = Fake(), Lease(), []
    result = measure(r, "qwen", LAYOUT, minimum=16384, lease=lease, progress=lambda a, b: steps.append((a, b)))
    assert r.calls[:2] == [8192, 16384] and r.calls[-1] == 32768 and len(r.calls) <= 5
    assert result.curve.maximum == r.calls[-2] and result.curve.maximum <= LAYOUT.window
    assert result.digest == "digest" and result.placement == "gpu" and not lease.retained
    assert steps == [(1, 4), (2, 4), (3, 4)]


def test_failed_candidate_falls_back_to_largest_verified_and_always_restores():
    r, lease = Fake(), Lease(); r.bad_candidates = True
    result = measure(r, "qwen", LAYOUT, minimum=16384, lease=lease)
    assert len(r.calls) == 5 and r.calls[-1] == 32768
    assert result.curve.maximum == 16384 and not lease.retained
    r, lease = Fake(), Lease(); r.bad_candidates = True
    with pytest.raises(Unsupported): measure(r, "qwen", LAYOUT, minimum=24576, lease=lease)
    assert r.calls[-1] == 32768 and not lease.retained


def test_initially_unloaded_target_returns_to_unloaded_and_admin_cap_limits_proposal():
    r, lease = Fake(previous=None), Lease()
    result = measure(r, "qwen", LAYOUT, minimum=16384, admin_limit=16384, lease=lease)
    assert r.calls == [8192, 16384, "unload"] and result.curve.maximum == 16384


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_timeout_retains_ownership_and_does_not_attempt_unsafe_restore(stage):
    r, lease = Fake(), Lease(); r.fault = stage
    with pytest.raises(Uncertain): measure(r, "qwen", LAYOUT, minimum=16384, lease=lease)
    assert len(r.calls) == stage and lease.retained


def test_failed_restore_is_not_success_and_retains_ownership():
    r, lease = Fake(), Lease(); r.restore_error = True
    with pytest.raises(Uncertain): measure(r, "qwen", LAYOUT, minimum=16384, lease=lease)
    assert lease.retained and r.uncertain


def test_bad_budget_or_admin_limit_refuses_before_model_loads():
    r, lease = Fake(), Lease()
    with pytest.raises(Unsupported): measure(r, "qwen", LAYOUT, minimum=16384, admin_limit=8192, lease=lease)
    r.available = lambda: (8_000_000_000, 1_000_000_000)
    with pytest.raises(Unsupported): measure(r, "qwen", LAYOUT, minimum=16384, lease=lease)
    assert not r.calls and not lease.retained

@pytest.mark.parametrize("stage", [2])
def test_placement_change_restores_original_state_and_refuses_measurement(stage):
    r, lease = Fake(), Lease(); original = r.cold_load
    def changed(model, window):
        point, place = original(model, window)
        return point, "mixed" if len(r.calls) == stage else place
    r.cold_load = changed
    with pytest.raises(Unsupported): measure(r, "qwen", LAYOUT, minimum=16384, lease=lease)
    assert r.calls[-1] == 32768 and not lease.retained


@pytest.mark.parametrize("stage", [1, 3])
def test_identity_change_cannot_be_called_restored(stage):
    r, lease = Fake(), Lease(); original = r.metadata
    def changed(model):
        result = original(model)
        return (*result[:3], "changed") if len(r.calls) >= stage else result
    r.metadata = changed
    with pytest.raises(Uncertain): measure(r, "qwen", LAYOUT, minimum=16384, lease=lease)
    assert lease.retained and r.uncertain


def test_wrong_restored_placement_is_not_success():
    r, lease = Fake(), Lease(); original = r.cold_load
    def changed(model, window):
        point, place = original(model, window)
        return point, "cpu" if len(r.calls) == 4 else place
    r.cold_load = changed
    with pytest.raises(Uncertain): measure(r, "qwen", LAYOUT, minimum=16384, lease=lease)
    assert lease.retained


def test_memory_pressure_between_probes_stops_before_loading_and_restores_previous():
    r, lease = Fake(), Lease()
    r.available = lambda: (32_000_000_000, 28_000_000_000 if not r.calls else 1_000_000_000)
    with pytest.raises(Unsupported, match="memory changed"):
        measure(r, "qwen", LAYOUT, minimum=16384, lease=lease)
    assert r.calls == [8192, 32768] and not lease.retained


def test_memory_pressure_after_slope_probes_cannot_trigger_an_oversized_candidate():
    r, lease = Fake(), Lease()
    r.available = lambda: (32_000_000_000, 28_000_000_000 if len(r.calls) < 2 else 1_000_000_000)
    with pytest.raises(Unsupported, match="available memory"):
        measure(r, "qwen", LAYOUT, minimum=16384, lease=lease)
    assert r.calls == [8192, 16384, 32768] and not lease.retained


@pytest.mark.parametrize("spill_both", [False, True])
def test_candidate_spill_steps_down_and_restores(spill_both):
    r, lease = Fake(), Lease(); original = r.cold_load
    def changed(model, window):
        point, place = original(model, window)
        spill = len(r.calls) == 3 or (spill_both and len(r.calls) == 4)
        return point, "mixed" if spill else place
    r.cold_load = changed
    result = measure(r, "qwen", LAYOUT, minimum=16384, lease=lease)
    assert r.calls[3] < r.calls[2] and r.calls[-1] == 32768
    assert result.curve.maximum == (16384 if spill_both else r.calls[3])
    assert result.placement == "gpu" and not lease.retained


@pytest.mark.parametrize("baseline,slope,bpt,weights", [(4_800_000_000, 37376, 32768, 5_160_000_000), (2_072_000_000, 80500, 81920, 2_240_000_000)])
def test_real_16gb_mac_resident_replacement_probes_do_not_double_count_cache(baseline, slope, bpt, weights):
    r, lease = Fake(), Lease(); current = [32768]; original = r.cold_load
    layout = Geometry("qwen35", 262144, 8, bpt)
    metadata = r.metadata
    r.metadata = lambda model: ({"size": weights}, *metadata(model)[1:])
    def row(model):
        size = baseline + current[0] * slope
        return {"context_length": current[0], "size": size, "size_vram": size, "digest": "digest"}
    r.resident = row
    r.reclaimable = lambda resident: resident["size"] if resident else 0
    # Captured Mac: 17.18 GB total, 1.40 GB free, 6.03 GB already resident.
    capacity = 1_400_000_000 + row("qwen")["size"]
    r.available = lambda: (17_180_000_000, capacity - row("qwen")["size"])
    def load(model, window):
        original(model, window); current[0] = window
        return Sample(window, baseline + window * slope), "gpu"
    r.cold_load = load
    result = measure(r, "qwen", layout, minimum=20480, lease=lease)
    assert r.calls == [8192, 16384, 32768]
    assert result.samples[2].window == 32768
    assert result.curve.maximum == 32768 and current[0] == 32768 and not lease.retained


def test_unloaded_default_is_not_evidence_that_a_probe_fits():
    r, lease = Fake(previous=None), Lease()
    r.available = lambda: (17_180_000_000, 5_950_000_000)
    with pytest.raises(Unsupported): measure(r, "qwen", LAYOUT, minimum=16384, lease=lease)
    assert not r.calls and not lease.retained


def test_one_probe_uses_observed_allocation_before_unproven_larger_probe():
    r, lease = Fake(previous=8192), Lease(); r.bad_candidates = True
    r.available = lambda: (17_180_000_000, 7_000_000_000)
    with pytest.raises(Unsupported): measure(r, "qwen", LAYOUT, minimum=16384, lease=lease)
    assert r.calls == [8192, 8192] and not lease.retained


@pytest.mark.parametrize("changes", [{"digest": "different"}, {"size": 6_800_000_000, "size_vram": 6_800_000_000}, {"size_vram": None}, {"size_vram": 1}])
def test_resident_sample_requires_matching_identity_allocation_and_placement(changes):
    r, lease = Fake(), Lease(); original = r.resident
    r.resident = lambda model: {**original(model), "digest": "digest", **changes}
    r.available = lambda: (17_180_000_000, 7_000_000_000)
    load = r.cold_load
    def restored(model, window):
        point, place = load(model, window)
        return point, "mixed" if window == 32768 and changes.get("size_vram") == 1 else place
    r.cold_load = restored
    with pytest.raises(Unsupported): measure(r, "qwen", LAYOUT, minimum=20480, lease=lease)
    if changes.get("size_vram", 0) is None:
        assert not r.calls
    else:
        assert r.calls[-1] == 32768


def test_unloaded_pressure_refusal_explains_how_to_establish_resident_evidence():
    r, lease = Fake(previous=None), Lease(); r.available = lambda: (17_180_000_000, 5_950_000_000)
    with pytest.raises(Unsupported, match="Send a message with this model first"):
        measure(r, "qwen", LAYOUT, minimum=20480, lease=lease)
    assert not r.calls
