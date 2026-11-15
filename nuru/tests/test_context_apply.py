"""Fault injection for Apply, without touching Ollama or the user's settings."""
from contextlib import contextmanager

import pytest

from core import context_apply, context_preferences as prefs
from core.context_memory import Identity, Geometry, Sample, calibrate, Unsupported


def setup(tmp_path):
    layout = Geometry("llama", 131072, 32, 131072)
    c = calibrate(Identity("digest", "version", "f16"), layout,
                  Sample(8192, 5_073_741_824), Sample(16384, 6_147_483_648))
    events = []; runtime = [8192]; held = [False]
    @contextmanager
    def reservation():
        assert not held[0]; held[0] = True; events.append("reserve")
        try: yield
        finally: held[0] = False; events.append("release")
    def reload(window):
        assert held[0]; events.append(("reload", window))
    def verify(window):
        assert held[0]; events.append(("verify", window)); return True
    def set_live(window):
        assert held[0]; runtime[0] = window; events.append(("live", window))
    def publish(*args, **kwargs):
        assert held[0]; events.append("publish"); return prefs.publish(*args, **kwargs)
    args = dict(minimum=4096, reservation=reservation, live_window=lambda: runtime[0],
                reload=reload, verify=verify, set_live=set_live, root=tmp_path, publish=publish)
    return c, events, runtime, held, args


def test_apply_verifies_before_publication_and_releases_reservation(tmp_path):
    c, events, runtime, held, args = setup(tmp_path)
    result = context_apply.apply("llama:latest", prefs.INITIAL, 2, c, **args)
    assert result.saved and result.notice is None and result.window == runtime[0] == 15258
    assert events == ["reserve", ("reload", 15258), ("verify", 15258), ("live", 15258), "publish", "release"]
    assert not held[0] and prefs.read("llama:latest", tmp_path).tokens == 15258


def test_stale_revision_and_invalid_choice_do_not_load_anything(tmp_path):
    c, events, runtime, held, args = setup(tmp_path)
    with pytest.raises(prefs.Conflict): context_apply.apply("llama", "stale", 1, c, **args)
    assert events == ["reserve", "release"]
    events.clear()
    with pytest.raises(Unsupported): context_apply.apply("llama", prefs.INITIAL, 0, c, **args)
    assert events == [] and not held[0]
    args["live_window"] = lambda: None
    with pytest.raises(Unsupported): context_apply.apply("llama", prefs.INITIAL, 1, c, **args)
    assert events == ["reserve", "release"]


@pytest.mark.parametrize("failure", ["reload", "verify", "unknown", "set_live"])
def test_failed_application_rolls_back_and_never_publishes(tmp_path, failure):
    c, events, runtime, held, args = setup(tmp_path)
    original = args.get(failure)
    def fail(window):
        if window != 8192:
            if failure == "unknown": return None
            raise OSError("fault")
        return original(window) if original else True
    if failure == "unknown": args["verify"] = fail
    else: args[failure] = fail
    with pytest.raises(context_apply.ApplyFailed) as error:
        context_apply.apply("llama", prefs.INITIAL, 2, c, **args)
    assert not error.value.uncertain and runtime[0] == 8192 and not held[0]
    assert "publish" not in events and prefs.read("llama", tmp_path).revision == prefs.INITIAL


@pytest.mark.parametrize("rollback", ["reload", "verify", "set_live"])
def test_failed_rollback_reports_uncertainty_preserves_disk_and_releases(tmp_path, rollback):
    c, events, runtime, held, args = setup(tmp_path)
    def verify(window): return window == 8192 and rollback != "verify"
    args["verify"] = verify
    if rollback != "verify":
        def fail(window):
            if window == 8192: raise OSError("rollback failed")
        args[rollback] = fail
    with pytest.raises(context_apply.ApplyFailed) as error:
        context_apply.apply("llama", prefs.INITIAL, 2, c, **args)
    assert error.value.uncertain and not held[0] and "publish" not in events
    assert prefs.read("llama", tmp_path).revision == prefs.INITIAL


def test_publish_failure_keeps_verified_runtime_and_next_rollback_uses_it(tmp_path):
    c, events, runtime, held, args = setup(tmp_path)
    def fail(*args, **kwargs): raise OSError("disk full")
    args["publish"] = fail
    result = context_apply.apply("llama", prefs.INITIAL, 2, c, **args)
    assert not result.saved and "couldn't be saved" in result.notice and runtime[0] == 15258
    assert prefs.read("llama", tmp_path).revision == prefs.INITIAL
    args["verify"] = lambda window: window == 15258
    with pytest.raises(context_apply.ApplyFailed) as error:
        context_apply.apply("llama", prefs.INITIAL, 3, c, **args)
    assert not error.value.uncertain and runtime[0] == 15258 and not held[0]
