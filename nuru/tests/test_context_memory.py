"""Model-free checks: unsupported sizing fails closed, never silently guesses a window."""
from dataclasses import replace

import pytest
from core.context_memory import Geometry, Identity, Sample, Unsupported, calibrate, geometry


def shown(arch="llama", **changes):
    fields = {"block_count": 32, "attention.head_count_kv": 8, "attention.key_length": 128,
              "attention.value_length": 128, "context_length": 131072}
    fields.update(changes)
    return {"model_info": {"general.architecture": arch, **{f"{arch}.{k}": v for k, v in fields.items()}}}


def test_geometry_counts_k_and_v_and_hybrid_full_attention_only():
    dense = geometry(shown(), "f16")
    assert dense == Geometry("llama", 131072, 32, 131072)
    for arch in ("qwen2", "qwen3"):
        assert geometry(shown(arch), "f16").bytes_per_token == dense.bytes_per_token
    for arch in ("qwen35", "qwen35moe"):
        hybrid = geometry(shown(arch, full_attention_interval=4), "f16")
        assert hybrid.attention_layers == 8 and hybrid.bytes_per_token == 32768
        flags = [True] * 32; flags[0] = False
        explicit = geometry(shown(arch, **{"attention.recurrent_layers": flags, "full_attention_interval": 4}), "f16")
        assert explicit.attention_layers == 1 and explicit.bytes_per_token == 4096


@pytest.mark.parametrize("arch,changes", [
    ("gemma3", {}), ("llama", {"block_count": 2048}), ("llama", {"attention.key_length": None}),
    ("llama", {"attention.head_count_kv": True}), ("llama", {"context_length": 0}),
    ("llama", {"attention.sliding_window": 4096}), ("llama", {"attention.shared_kv_layers": 1}),
    ("llama", {"nextn_predict_layers": 1}), ("qwen35", {}),
    ("qwen35", {"full_attention_interval": 64}), ("qwen35", {"attention.recurrent_layers": [True] * 32}),
    ("qwen35", {"attention.recurrent_layers": [0] * 32}), ("qwen35", {"attention.recurrent_layers": [False]}),
])
def test_missing_or_unsupported_metadata_refuses(arch, changes):
    with pytest.raises(Unsupported): geometry(shown(arch, **changes), "f16")


@pytest.mark.parametrize("cache", [None, "", "q8_0", "q4_0", "unknown"])
def test_server_precision_is_never_inferred(cache):
    with pytest.raises(Unsupported): geometry(shown(), cache)


def test_missing_info_and_identity_are_rejected():
    with pytest.raises(Unsupported): geometry({}, "f16")
    for value in (None, "", " ", "x" * 257):
        with pytest.raises(Unsupported): Identity(value, "v1", "f16")


def calibrated():
    layout = geometry(shown(), "f16"); identity = Identity("sha256:digest", "0.99", "f16")
    return calibrate(identity, layout, Sample(8192, 4_000_000_000 + 8192 * 131072),
                     Sample(16384, 4_000_000_000 + 16384 * 131072))


def test_calibration_and_decimal_gb_rounding_limits_and_identity_invalidation():
    c = calibrated()
    assert c.bytes_per_token == 131072
    assert c.tokens(1, minimum=4096) == 7629
    assert c.tokens(100, minimum=4096) == 131072
    assert c.tokens(100, minimum=4096, admin_limit=32768) == 32768
    assert c.matches(c.identity, c.geometry)
    for identity in (replace(c.identity, digest="different"), replace(c.identity, server_version="new"), replace(c.identity, kv_type="q8_0")):
        assert not c.matches(identity, c.geometry)
    assert not c.matches(c.identity, replace(c.geometry, window=262144))
    for args in ((0, 4096, None), (True, 4096, None), (1, 0, None), (1, 8192, None), (100, 4096, 1024), (1, 4096, -1)):
        gb, minimum, limit = args
        with pytest.raises(Unsupported): c.tokens(gb, minimum=minimum, admin_limit=limit)


@pytest.mark.parametrize("low,high", [
    (Sample(8192, 1), Sample(32768, 2)), (Sample(8192, 1), Sample(8192, 2)),
    (Sample(8192, 100), Sample(16384, 99)), (Sample(8192, True), Sample(16384, 100)),
    (Sample(8192, 1), Sample(16384, 2)),
    (Sample(8192, 1), Sample(16384, 1 + 8192 * 131072)),
])
def test_bad_contaminated_or_inconsistent_samples_refuse(low, high):
    c = calibrated()
    with pytest.raises(Unsupported): calibrate(c.identity, c.geometry, low, high)


def test_calibration_accepts_small_alignment_noise_but_not_wrong_kv_geometry():
    c = calibrated(); base = 4_000_000_000
    result = calibrate(c.identity, c.geometry, Sample(8192, base + 8192 * 131072),
                       Sample(16384, base + 16384 * 131072 + 4096))
    assert result.bytes_per_token > c.bytes_per_token
    with pytest.raises(Unsupported):
        calibrate(c.identity, c.geometry, Sample(8192, base), Sample(16384, base + 8192 * 65536))

def test_calibration_refuses_a_precision_identity_that_does_not_match_the_formula():
    c = calibrated()
    with pytest.raises(Unsupported):
        calibrate(replace(c.identity, kv_type="q8_0"), c.geometry, Sample(8192, 5_073_741_824), Sample(16384, 6_147_483_648))
