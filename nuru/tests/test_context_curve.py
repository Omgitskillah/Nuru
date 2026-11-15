"""Effective allocation uses measurements, not guessed KV precision or file weights."""
import pytest
from core.context_curve import fit, candidate
from core.context_memory import Geometry, Sample, Unsupported

LAYOUT = Geometry("qwen35", 262144, 8, 32768)
LOW = Sample(8192, 5109721332)
HIGH = Sample(16384, 5415905524)
TOP = Sample(32768, 6028284394)


def test_measured_qwen_curve_and_verified_envelope():
    c = fit(LAYOUT, LOW, HIGH, TOP)
    assert c.slope == 37376
    assert c.intercept == 4803537140
    assert c.maximum == 32768 and c.agrees(TOP)
    assert not c.agrees(Sample(65536, 1))
    assert not c.agrees(Sample(True, 1)) and not c.agrees(Sample(16384, True))
    assert not c.agrees(Sample(16384, HIGH.size - 400_000_000))
    assert c.tokens(1, minimum=16384) == 26755
    assert c.tokens(100, minimum=16384) == 32768
    assert c.tokens(100, minimum=8192, admin_limit=16384) == 16384
    assert c.limits(16384) == (1, 2)
    assert c.limits(8192, 16384) == (1, 1)
    for args in ((0, 8192, None), (1, 32768, None), (1, 8192, 0)):
        gb, minimum, cap = args
        with pytest.raises(Unsupported): c.tokens(gb, minimum=minimum, admin_limit=cap)
    with pytest.raises(Unsupported): c.limits(65536)


def test_granite_measured_wobble_is_checked_against_context_not_total_weights():
    layout = Geometry("granite", 131072, 40, 81920)
    c = fit(layout, Sample(8192, 2715686010), Sample(16384, 3403551866), Sample(32768, 4708389354))
    assert c.agrees(Sample(16384, 3315859455))
    assert not c.agrees(Sample(16384, 3403551866 - 300_000_000))


@pytest.mark.parametrize("low,high,top", [
    (Sample(4096, 1), HIGH, TOP), (LOW, Sample(8192, 1), TOP),
    (LOW, HIGH, Sample(12288, 1)), (LOW, Sample(16384, LOW.size), TOP),
    (LOW, HIGH, Sample(32768, TOP.size + 1_000_000_000)),
    (Sample(8192, 1), Sample(16384, 8193), Sample(32768, 24577)),
    (LOW, Sample(16384, LOW.size + 8192 * 10_000_000), TOP),
])
def test_bad_or_inconsistent_measurements_refuse(low, high, top):
    with pytest.raises(Unsupported): fit(LAYOUT, low, high, top)


def test_too_small_allocation_cannot_hide_precision_change_behind_absolute_tolerance():
    layout = Geometry("llama", 32768, 1, 10000)
    with pytest.raises(Unsupported, match="too small"):
        fit(layout, Sample(8192, 2_081_920_000), Sample(16384, 2_163_840_000), Sample(32768, 2_327_680_000))
    with pytest.raises(Unsupported, match="baseline"):
        fit(LAYOUT, Sample(8192, 1), Sample(16384, 1 + 8192 * 32768), Sample(32768, 1 + 24576 * 32768))


def test_candidate_uses_available_memory_and_physical_ceiling_once_never_extrapolates_as_verification():
    c = fit(LAYOUT, LOW, HIGH, TOP)
    proposed = candidate(c, total=16_000_000_000, available=14_000_000_000, maximum=262144, minimum=16384)
    assert proposed == 113664 and proposed > c.maximum
    assert candidate(c, total=16_000_000_000, available=14_000_000_000, maximum=65536, minimum=16384) == 65536
    for total, available in ((None, 10), (16_000_000_000, None), (16_000_000_000, 1_000_000_000)):
        with pytest.raises(Unsupported): candidate(c, total=total, available=available, maximum=262144, minimum=16384)
