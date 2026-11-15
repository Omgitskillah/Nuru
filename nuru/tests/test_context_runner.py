"""No real model operations: bounded request bodies, residency checks and uncertainty."""
from types import SimpleNamespace
import pytest
import requests
from core.context_runner import Runner, Uncertain
from core.context_memory import Sample, Unsupported


class HTTP:
    def __init__(self):
        self.calls = []; self.rows = []; self.error = None; self.version = "0.40.11"
        self.tags = [{"name": "nuru:latest", "digest": "digest", "size": 4_000_000_000}]
        self.shown = {"model_info": {"general.architecture": "qwen35"}, "parameters": "params"}
    def response(self, body): return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)
    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response({"/api/ps": {"models": self.rows}, "/api/version": {"version": self.version}, "/api/tags": {"models": self.tags}}[url.removeprefix("http://test")])
    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if self.error: raise self.error
        if url.endswith("/api/show"): return self.response(self.shown)
        body = kwargs["json"]
        self.rows = [] if body.get("keep_alive") == 0 else [{"name": "nuru:latest", "context_length": body["options"]["num_ctx"], "size": 5_000_000_000, "size_vram": 5_000_000_000}]
        return self.response({})


def setup():
    http = HTTP()
    return Runner(lambda model: "nuru", http=http, base="http://test", memory=lambda: SimpleNamespace(total=16_000_000_000, available=10_000_000_000)), http


def test_metadata_and_memory_are_read_only_and_fingerprinted():
    r, h = setup(); assert not h.calls
    shown, digest, version, key = r.metadata("qwen")
    assert digest == "digest" and version == "0.40.11" and len(key) == 64 and shown["size"] == 4_000_000_000
    h.shown["parameters"] = "changed"; assert r.metadata("qwen")[3] != key
    assert all(not url.endswith("generate") for _, url, _ in h.calls)
    assert r.available() == (16_000_000_000, 10_000_000_000)


def test_load_is_bounded_empty_and_actual_context_and_gpu_are_verified():
    r, h = setup(); sample, placement = r.load("qwen", 16384)
    assert sample == Sample(16384, 5_000_000_000) and placement == "gpu"
    assert [c for c in h.calls if c[0] == "POST"][0][2] == {"json": {"model": "nuru", "options": {"num_ctx": 16384}, "keep_alive": "30m", "stream": False}, "timeout": (10, 300)}
    for vram, expected in ((0, "cpu"), (1, "mixed")):
        h.rows[0]["size_vram"] = vram; assert r.verify("qwen", 16384)[1] == expected
    r.unload_owned("qwen"); assert h.rows == []


@pytest.mark.parametrize("rows", [[{"name": "another"}], [None], [{"name": "nuru"}, {"name": "nuru:latest"}], None])
def test_other_or_invalid_residency_refuses_before_load(rows):
    r, h = setup(); h.rows = rows
    with pytest.raises(Unsupported): r.load("qwen", 16384)
    assert not [c for c in h.calls if c[0] == "POST"]


@pytest.mark.parametrize("changes", [{"context_length": 0}, {"context_length": True}, {"size": None}, {"size_vram": None}, {"size_vram": 6_000_000_000}])
def test_unknown_residency_is_not_assumed_success(changes):
    r, h = setup(); r.load("qwen", 16384); h.rows[0].update(changes)
    with pytest.raises(Unsupported): r.verify("qwen", 16384)
    h.rows = []
    with pytest.raises(Unsupported): r.verify("qwen", 16384)


def test_timed_out_load_is_uncertain_and_never_automatically_retried_or_unloaded():
    r, h = setup(); h.error = requests.Timeout("server may still be working")
    with pytest.raises(Uncertain): r.load("qwen", 16384)
    assert r.uncertain
    with pytest.raises(Uncertain): r.load("qwen", 16384)
    with pytest.raises(Uncertain): r.unload_owned("qwen")
    assert len([c for c in h.calls if c[0] == "POST"]) == 1


def test_unload_failure_or_unconfirmed_restoration_is_explicit():
    r, h = setup(); h.error = requests.Timeout()
    with pytest.raises(Uncertain): r.unload_owned("qwen")
    r, h = setup(); h.post = lambda *args, **kwargs: h.response({}); h.rows = [{"name": "nuru"}]
    with pytest.raises(Unsupported): r.unload_owned("qwen")


@pytest.mark.parametrize("field,value", [("version", None), ("tags", []), ("tags", [{"name": "nuru", "digest": None}]), ("shown", [])])
def test_unknown_identity_refuses(field, value):
    r, h = setup(); setattr(h, field, value)
    with pytest.raises(Unsupported): r.metadata("qwen")


def test_cold_load_and_reclaimable_memory_do_not_treat_dedicated_vram_as_free_ram(monkeypatch):
    import core.context_runner as module
    r, h = setup(); r.cold_load("qwen", 16384)
    monkeypatch.setattr(module.sys, "platform", "linux")
    assert r.reclaimable(None) == 0
    assert r.reclaimable({"size": 100, "size_vram": 100}) == 0
    assert r.reclaimable({"size": 100, "size_vram": 0}) == 100
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.platform, "machine", lambda: "arm64")
    assert r.reclaimable({"size": 100, "size_vram": 100}) == 100


def test_available_memory_signal_on_this_platform_is_real_and_bounded():
    total, available = Runner(lambda model: model).available()
    assert type(total) is int and type(available) is int and 0 < available <= total


def test_normal_chat_lookup_tolerates_embedding_residency_but_measurement_does_not():
    r, h = setup(); r.load("qwen", 16384)
    h.rows.append({"name": "embedding:latest", "size": 100, "size_vram": 100})
    assert r.resident("qwen", exclusive=False)["name"] == "nuru:latest"
    assert r.verify("qwen", 16384, exclusive=False)[0].window == 16384
    with pytest.raises(Unsupported): r.resident("qwen")
    with pytest.raises(Unsupported): r.load("qwen", 32768)


@pytest.mark.parametrize("timeout", [0, -1, True, float("inf"), float("nan"), 3601])
def test_invalid_load_wait_is_rejected(timeout):
    with pytest.raises(ValueError): Runner(lambda model: model, load_timeout=timeout)


def test_cleanup_can_leave_other_resident_models_untouched():
    r, h = setup(); r.load("qwen", 16384)
    h.rows.append({"name": "embedding:latest"})
    original = h.post
    def unload(url, **kwargs):
        response = original(url, **kwargs)
        h.rows = [{"name": "embedding:latest"}]
        return response
    h.post = unload
    r.unload_owned("qwen", exclusive=False)
    assert h.rows == [{"name": "embedding:latest"}]
    assert h.calls[-2][2]["json"] == {"model": "nuru", "keep_alive": 0}
