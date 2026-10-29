"""F13: LayaPredictor and laya-mcp-fetch (laya_mcp.predictor), against a stand-in `laya`.

The real package (the optional `model` extra) brings torch and downloads checkpoints, so CI doesn't
install it. The stand-in follows laya 0.3.21's API as its source has it: Router(device=, revision=,
max_loaded=), .preload(names), .load(name) -> an Agent with .device/.tok/.cfg, .loaded, .route(state,
questions) -> a dict (RouteDecision) with "model", .predict(state, questions, model=, max_len=,
head_max_len=, on_predict_end=) -> {"answers": ...}; laya.agent.Agent._to_internal (a staticmethod);
laya.common.encode_text(tok, text, **kw) -> tok(text, **kw), serialize_state, and
build_sequence(tok, state, q, max_len, head_max_len, return_stats=True) -> (ids, markers, stats).
"""
import importlib.metadata
import json
import os
import sys
import threading
import types

import pytest

from laya_mcp import predictor as predictor_module
from laya_mcp.config import load_config
from laya_mcp.predictor import LayaPredictor, OverBudget, Prediction, Unavailable
from laya_mcp.question_sets import load_question_sets


class Tokenizer:
    mask_token = "[MASK]"

    def __call__(self, text, add_special_tokens=True):
        return {"input_ids": text.split()}  # one token a word


class Agent:
    def __init__(self, name, cfg):
        self.name, self.device, self.tok, self.cfg = name, f"cpu:{name}", Tokenizer(), cfg

    @staticmethod
    def _to_internal(qdef):
        return {"options": qdef.get("options", ["a", "b"]), "instructions": qdef.get("instructions", "")}


class Router:
    made = []
    fail = None
    route_to = "multilingual"
    elapsed_ms = 12.5

    def __init__(self, models=None, device=None, token=None, revision=None, revisions=None, max_loaded=2, **kwargs):
        if Router.fail:
            raise Router.fail
        self.device, self.revision, self.max_loaded, self.preloaded, self.calls = device, revision, max_loaded, [], []
        self._agents = {}
        Router.made.append(self)

    def preload(self, names=None):
        self.preloaded.extend(names or [])

    def load(self, name):
        return self._agents.setdefault(name, Agent(name, {"max_len": 40, "head_max_len": 8}))

    @property
    def loaded(self):
        return list(self._agents)

    def route(self, state, questions=None, **kwargs):
        return {"model": Router.route_to, "reason": "script"}

    def predict(self, state, questions, model=None, max_len=None, head_max_len=None, on_predict_end=None, **kwargs):
        self.calls.append({"model": model, "max_len": max_len, "head_max_len": head_max_len})
        if on_predict_end:
            on_predict_end(types.SimpleNamespace(elapsed_ms=Router.elapsed_ms))
        return {"answers": {qid: {"choice": "a"} for qid in questions}}


def build_sequence(tok, state, q, max_len=512, head_max_len=192, return_stats=False, **kwargs):
    ids = ["[CLS]", *tok(q["instructions"])["input_ids"], *q["options"], "[SEP]", *tok(state)["input_ids"]]
    stats = {"options": len(q["options"]), "options_distinct": len(set(q["options"]))}
    return (ids, [], stats) if return_stats else (ids, [])


@pytest.fixture(autouse=True)
def laya(monkeypatch):
    Router.made, Router.fail, Router.route_to, Router.elapsed_ms = [], None, "multilingual", 12.5
    package = types.ModuleType("laya")
    package.Router = Router
    agent = types.ModuleType("laya.agent")
    agent.Agent = Agent
    common = types.ModuleType("laya.common")
    common.build_sequence = build_sequence
    common.encode_text = lambda tok, text, **kwargs: tok(text, **kwargs)
    common.serialize_state = lambda state: state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    for name, module in (("laya", package), ("laya.agent", agent), ("laya.common", common)):
        monkeypatch.setitem(sys.modules, name, module)
    real_version = importlib.metadata.version
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.3.21" if name == "laya" else real_version(name))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)


SETTINGS = load_config().laya
QSETS = load_question_sets()


def qset(checkpoint="english", **changes):
    base = next(iter(QSETS.values()))
    return base.model_copy(update={"checkpoint": checkpoint, **changes})


def loaded(settings=SETTINGS):
    predictor = LayaPredictor(settings)
    predictor.start()
    for thread in threading.enumerate():
        if thread.name == "laya-load":
            thread.join(5)
    return predictor


def test_loading_preloads_every_checkpoint_offline_and_reports_ready():
    predictor = loaded(SETTINGS.model_copy(update={"device": "auto", "offline": True, "revision": "abc123"}))
    (router,) = Router.made
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert (router.device, router.revision, router.max_loaded) == (None, "abc123", len(SETTINGS.checkpoints))
    assert router.preloaded == list(SETTINGS.checkpoints)
    assert predictor.status() == {"state": "ready", "error": None, "device": f"cpu:{SETTINGS.checkpoints[0]}",
                                  "loaded": [SETTINGS.checkpoints[0]], "revision": "abc123", "laya_version": "0.3.21"}


def test_a_chosen_device_is_passed_on_and_online_loading_leaves_the_hub_alone():
    loaded(SETTINGS.model_copy(update={"device": "cpu", "offline": False}))
    assert Router.made[0].device == "cpu" and "HF_HUB_OFFLINE" not in os.environ


def test_before_loading_and_after_a_failed_load_every_prediction_is_refused():
    waiting = LayaPredictor(SETTINGS)
    assert waiting.status()["state"] == "not started" and waiting.status()["loaded"] == []
    with pytest.raises(Unavailable, match="Laya is not started"):
        waiting.predict(qset(), "state", {})
    Router.fail = OSError("no checkpoints offline")
    failed = loaded()
    assert failed.status()["state"] == "failed" and failed.status()["error"] == "OSError: no checkpoints offline"
    with pytest.raises(Unavailable, match=r"Laya is failed \(OSError: no checkpoints offline\)"):
        failed.predict(qset(), "state", {})


def test_an_english_set_goes_where_the_router_sends_it_and_another_set_to_its_own_checkpoint():
    predictor = loaded()
    questions = {"q": {"instructions": "Is it done?", "options": ["yes", "no"]}}
    routed = predictor.predict(qset("english"), "Das ist fertig", questions)
    assert routed == Prediction({"q": {"choice": "a"}}, "multilingual", 3, 12.5)
    fixed = predictor.predict(qset("typed-decisions", max_len=30, head_max_len=6), {"note": "done"}, questions)
    assert fixed.checkpoint == "typed-decisions"
    assert Router.made[0].calls[-1] == {"model": "typed-decisions", "max_len": 30, "head_max_len": 6}


def test_without_a_measured_latency_the_wall_clock_is_used():
    Router.elapsed_ms = 0.0
    result = loaded().predict(qset("multilingual"), "state", {"q": {"instructions": "x"}})
    assert result.latency_ms > 0


@pytest.mark.parametrize("changes, state, questions, message", [
    ({"max_state_tokens": 2}, "three words here", {}, "state needs 3 tokens, over the 2-token budget"),
    ({}, "s", {"q": {"instructions": "x", "options": ["same", "same"]}}, "q's options \\(collapsed\\) needs 2 tokens"),
    ({"max_len": 5}, "a b c", {"q": {"instructions": "long question here", "options": ["y", "n"]}},
     "q with this state needs 10 tokens, over the 5-token budget"),
])
def test_an_input_laya_would_cut_off_is_refused_before_it_is_sent(changes, state, questions, message):
    predictor = loaded()
    with pytest.raises(OverBudget, match=message):
        predictor.predict(qset("multilingual", **changes), state, questions)
    assert Router.made[0].calls == []


def test_the_checkpoints_own_limits_apply_when_the_set_has_none():
    predictor = loaded()
    questions = {"q": {"instructions": " ".join(["w"] * 35), "options": ["y", "n"]}}
    with pytest.raises(OverBudget, match="over the 40-token budget"):  # the fake checkpoint's max_len
        predictor.predict(qset("multilingual", max_len=None), "a b c", questions)


def test_fetch_downloads_every_pinned_checkpoint_online(monkeypatch, capsys):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    predictor_module.fetch()
    (router,) = Router.made
    assert "HF_HUB_OFFLINE" not in os.environ and router.preloaded == list(SETTINGS.checkpoints)
    assert capsys.readouterr().out == f"Fetched {', '.join(SETTINGS.checkpoints)} at revision {SETTINGS.revision}.\n"
