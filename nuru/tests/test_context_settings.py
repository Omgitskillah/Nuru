"""F40's local administration boundary: metadata GET, exact one-shot mutation, no Session."""
import pytest
from core.service.context_settings import ContextSettings, validate
from core.service.protocol import ProtocolError
from core.context_memory import Unsupported
from core.context_preferences import Conflict
from test_service_host import harness
from test_service_server import served


def settings(model="qwen", **changes):
    return {"model": model, "revision": "initial", "window_tokens": 32768,
            "measured": False, "can_measure": True, "busy": False, **changes}


class Driver:
    def __init__(self): self.calls = []; self.info = settings()
    def describe(self, model): self.calls.append(("read", model)); return self.info
    def measure(self, model): self.calls.append(("measure", model)); return self.info
    def apply(self, model, gb, revision): self.calls.append(("apply", model, gb, revision)); return self.info


def controller(service):
    service._models = lambda: {"default": "qwen", "choices": [{"id": "qwen", "cloud": False}]}
    driver = Driver(); control = ContextSettings(service, driver); service.context_settings = control
    return control, driver


def test_get_is_metadata_only_and_put_is_exactly_one_explicit_action(harness):
    h = harness(); control, d = controller(h.service)
    assert control.describe("qwen") == settings()
    assert d.calls == [("read", "qwen")] and not h.built
    body = {"model": "qwen", "operation": "measure", "expected_revision": "initial"}
    assert control.change(body) == settings()
    assert d.calls[-1] == ("measure", "qwen") and not h.service._model_changing
    control.change({**body, "operation": "apply", "gb": 2})
    assert d.calls[-1] == ("apply", "qwen", 2, "initial")


@pytest.mark.parametrize("changes", [{"operation": "bad"}, {"gb": 1}, {"extra": "field"}, {"expected_revision": "stale"}, {"model": None}])
def test_invalid_change_cannot_reach_driver(harness, changes):
    h = harness(); control, d = controller(h.service)
    with pytest.raises(ProtocolError): control.change({"model": "qwen", "operation": "measure", "expected_revision": "initial", **changes})
    assert d.calls == []


def test_unknown_models_stale_revisions_and_busy_work_never_reload(harness):
    h = harness(); control, d = controller(h.service)
    with pytest.raises(ProtocolError): control.describe("unknown")
    with pytest.raises(ProtocolError): control.change({"model": "qwen", "operation": "measure", "expected_revision": "a" * 32})
    assert d.calls == [("read", "qwen")]
    with h.service.model_change():
        with pytest.raises(ProtocolError): control.change({"model": "qwen", "operation": "measure", "expected_revision": "initial"})
    assert d.calls == [("read", "qwen")]


def test_cloud_control_uses_provider_window_and_never_reaches_ollama(harness):
    from core.providers import claude
    model = claude.PREFIX + next(iter(claude.MAIN_MODELS))
    h = harness(); control, d = controller(h.service)
    h.service._models = lambda: {"default": model, "choices": [{"id": model, "cloud": True}]}
    info = control.describe(model)
    assert not info["can_measure"] and "Anthropic" in info["reason"]
    assert info["window_tokens"] == claude.model_info(claude.model_id(model))["context_window"]
    with pytest.raises(ProtocolError): control.change({"model": model, "operation": "measure", "expected_revision": "initial"})
    assert d.calls == []


@pytest.mark.parametrize("error", [Conflict("another editor"), Unsupported("unknown layout"), OSError("disk"), ValueError("bad")])
def test_driver_failures_release_reservation_and_do_not_retry(harness, error):
    h = harness(); control, d = controller(h.service)
    def fail(model): d.calls.append(("measure-failed", model)); raise error
    d.measure = fail
    with pytest.raises(ProtocolError): control.change({"model": "qwen", "operation": "measure", "expected_revision": "initial"})
    assert not h.service._model_changing and d.calls.count(("measure-failed", "qwen")) == 1


def test_malformed_driver_reads_and_model_binding_refuse(harness):
    h = harness(); control, d = controller(h.service)
    d.info = settings(model="wrong")
    with pytest.raises(ProtocolError): control.describe("qwen")
    with pytest.raises(ProtocolError): control.change({"model": "qwen", "operation": "measure", "expected_revision": "initial"})
    d.describe = lambda model: (_ for _ in ()).throw(OSError("offline"))
    with pytest.raises(ProtocolError): control.describe("qwen")
    with pytest.raises(ProtocolError): validate("settings", {**settings(), "private_path": "/secret"})


def test_context_http_contract_auth_body_and_metadata_no_models(served):
    client, service, _, built = served
    assert client.call("GET", "/v1/models/context?model=qwen")[0] == 503
    control, d = controller(service)
    assert client.call("GET", "/v1/models/context?model=qwen", token=None)[0] == 401
    assert client.call("GET", "/v1/models/context")[0] == 400
    assert client.call("GET", "/v1/models/context?model=qwen")[0] == 200
    body = {"model": "qwen", "operation": "apply", "expected_revision": "initial", "gb": 2}
    assert client.call("PUT", "/v1/models/context", body, headers={"Content-Type": "application/json"})[0] == 200
    assert d.calls[-1] == ("apply", "qwen", 2, "initial") and built == []
    assert client.call("PUT", "/v1/models/context", {**body, "gb": True}, headers={"Content-Type": "application/json"})[0] == 400


def test_uncertain_adapter_error_and_wrong_result_model_refuse(harness):
    from core.context_runner import Uncertain
    h = harness(); control, d = controller(h.service)
    d.measure = lambda model: (_ for _ in ()).throw(Uncertain("still loading"))
    with pytest.raises(ProtocolError, match="still loading"):
        control.change({"model": "qwen", "operation": "measure", "expected_revision": "initial"})
    d.measure = lambda model: settings("wrong")
    with pytest.raises(ProtocolError, match="different model"):
        control.change({"model": "qwen", "operation": "measure", "expected_revision": "initial"})
