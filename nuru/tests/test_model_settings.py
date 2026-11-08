"""The model settings routes (TODO F22, model-settings-v1.schema.json) over real loopback HTTP: a stand-in
Ollama for /api/show and /api/tags, recorded builds, a scratch ~/.nuru/models. What the plugin's settings
show, add, save and reset, the conflicts and refusals, and that only this computer's listener serves them."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import user_models
from core.providers import ollama
from core.service import model_settings
from core.service.credentials import Credentials
from core.service.host import Service
from core.service.server import NuruServer
from core.service.store import ServiceStore
from core.conversation_store import ConversationStore
from test_service_host import ScriptedSession
from test_service_server import Client

LLAMA = {"capabilities": ["completion", "tools", "thinking"], "parameters": 'stop "<|eot_id|>"\ntemperature 0.6'}
SHOWN = {"llama3.1:8b": LLAMA, "qwen3.5:4b": {"capabilities": ["completion", "tools", "thinking"]},
         "gemma4:e2b": {"capabilities": ["completion", "vision"]}, "llama3.1:70b": LLAMA}


class FakeOllama:
    """/api/show answers SHOWN (404 for anything else); /api/tags lists them and `extra` names."""

    def __init__(self):
        self.extra = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.reply(200, {"models": [{"name": n} for n in [*SHOWN, *fake.extra]]})

            def do_POST(self):
                name = json.loads(self.rfile.read(int(self.headers["content-length"])))["model"]
                self.reply(200, SHOWN[name]) if name in SHOWN else self.reply(404, {"error": f"model '{name}' not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def admin(tmp_path, monkeypatch):
    """(a client of this computer's listener, the builds asked of Ollama, the settings folder, a setter
    for the default model)."""
    fake = FakeOllama()
    monkeypatch.setattr(ollama, "BASE", fake.base)
    models = tmp_path / "models"
    monkeypatch.setenv("NURU_MODELS_DIR", str(models))
    monkeypatch.setattr(user_models, "_active", {})
    built, failing = [], {}
    def build(model, rendered):
        built.append((model, rendered))
        if failing:
            raise failing["error"]
        return rendered
    monkeypatch.setattr(ollama, "build_model", build)
    monkeypatch.setattr(ollama, "remove_model", lambda model: built.append(("rm", model)))
    credentials = Credentials(tmp_path / "service")
    token = credentials.add("obsidian")
    (tmp_path / "vault").mkdir()
    default = {"id": "qwen3.5:4b"}
    service = Service(ServiceStore(tmp_path / "service.db"), ConversationStore(tmp_path / "conversation.db"),
                      lambda c: ScriptedSession([]), tmp_path / "vault",
                      models=lambda: {"default": default["id"], "choices": []})
    server = NuruServer(service, credentials, port=0, keepalive_seconds=0.2)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Client(server.server_port, token), built, models, default, failing, (service, credentials)
    server.closing = True
    service.close()
    server.shutdown()
    server.server_close()
    service.store.close()
    service.conversations.close()
    fake.server.shutdown()
    fake.server.server_close()


def get(client, model):
    return client.call("GET", f"/v1/models/settings?model={model}")


def test_a_family_nuru_runs_shows_nuru_s_own_settings_to_start_from(admin):
    client, *_ = admin
    status, body, _ = get(client, "qwen3.5:4b")
    assert status == 200 and body["family"] == "qwen3.5" and body["default"] is True
    assert (body["custom"], body["revision"], body["active_revision"]) == (False, "initial", "initial")
    assert body["header"] == body["nuru"]["header"] and "FROM __BASE_MODEL__" in body["header"]
    assert "SYSTEM" not in body["header"] and "presence_penalty" in body["profile"]
    status, body, _ = get(client, "llama3.1:8b")
    assert body["nuru"] is None and body["header"] is None and body["default"] is False, "Nuru has no settings of its own"


def test_adding_a_pulled_model_sets_its_family_up_once_checked_and_built(admin):
    client, built, models, *_ = admin
    status, body, _ = client.call("POST", "/v1/models", {"model": "llama3.1:8b"})
    assert status == 200 and body["added"] is True and body["custom"] is True
    assert body["revision"] != "initial" and body["active_revision"] == "initial", "it applies when Nuru restarts"
    assert '#   stop "<|eot_id|>"' in body["header"] and "low: true" in body["profile"]
    [(check, rendered), (rm, removed)] = built
    assert rm == "rm" and check.startswith("nuru-check-") and removed == check and rendered.startswith("FROM llama3.1:8b\n")
    assert user_models.read("llama3.1", models).revision == body["revision"]
    status, again, _ = client.call("POST", "/v1/models", {"model": "llama3.1:70b"})
    assert status == 200 and again["added"] is False and again["revision"] == body["revision"] and len(built) == 2
    status, nuru, _ = client.call("POST", "/v1/models", {"model": "qwen3.5:4b"})
    assert nuru["added"] is False and nuru["custom"] is False, "Nuru's own family needs no setting up"


@pytest.mark.parametrize("model, status, problem", [
    ("gemma4:e2b", 400, "can't use tools"),
    ("mistral:7b", 404, "Ollama has no model mistral:7b: download it first"),
    ("../etc", 400, "not a model name"),
], ids=["no-tools", "not-pulled", "bad-name"])
def test_a_model_that_can_t_run_nuru_isn_t_added(admin, model, status, problem):
    client, built, models, *_ = admin
    answer, body, _ = client.call("POST", "/v1/models", {"model": model})
    assert answer == status and problem in body["error"]["message"] and built == []
    assert not models.exists() or not list(models.glob("*.json"))


def test_a_save_is_checked_built_and_stored_and_a_stale_one_is_refused(admin):
    client, built, models, *_ = admin
    first = client.call("POST", "/v1/models", {"model": "llama3.1:8b"})[1]
    header = "FROM __BASE_MODEL__\nPARAMETER temperature 0.4\n"
    save = {"model": "llama3.1:8b", "expected_revision": first["revision"], "header": header, "profile": first["profile"]}
    status, saved, _ = client.call("PUT", "/v1/models/settings", save)
    assert status == 200 and saved["header"] == header and saved["revision"] not in (first["revision"], "initial")
    assert "PARAMETER temperature 0.4" in built[-2][1]
    status, body, _ = client.call("PUT", "/v1/models/settings", {**save, "header": "FROM __BASE_MODEL__\n"})
    assert status == 409 and "reload" in body["error"]["message"], "another window saved first"
    assert user_models.read("llama3.1", models).header == header


@pytest.mark.parametrize("change, problem", [
    ({"header": "FROM __BASE_MODEL__\nSYSTEM mine\n"}, "line 2: SYSTEM isn't allowed"),
    ({"header": "FROM __BASE_MODEL__\nCAPABILITY vision\n"}, "capability 'vision'"),
    ({"profile": "default: sometimes\n"}, "think needs exactly"),
    ({"expected_revision": "nope"}, "expected_revision: not in the required format"),
    ({"extra": 1}, "has a field the schema doesn't allow"),
    ({"model": "gemma4:e2b"}, "can't use tools"),
], ids=["system", "capability", "profile", "revision-format", "extra-field", "other-model"])
def test_a_save_that_fails_a_check_stores_and_builds_nothing(admin, change, problem):
    client, built, models, *_ = admin
    first = client.call("POST", "/v1/models", {"model": "llama3.1:8b"})[1]
    builds = len(built)
    save = {"model": "llama3.1:8b", "expected_revision": first["revision"], "header": first["header"],
            "profile": first["profile"], **change}
    status, body, _ = client.call("PUT", "/v1/models/settings", save)
    assert status == 400 and problem in body["error"]["message"]
    assert len(built) == builds and user_models.read("llama3.1", models).revision == first["revision"]


def test_a_save_ollama_can_t_build_is_refused_with_what_it_said(admin):
    import subprocess
    client, built, models, _, failing, _ = admin
    first = client.call("POST", "/v1/models", {"model": "llama3.1:8b"})[1]
    failing["error"] = subprocess.CalledProcessError(1, ["ollama"], stderr="Error: unknown parameter")
    status, body, _ = client.call("PUT", "/v1/models/settings", {"model": "llama3.1:8b", "expected_revision": first["revision"],
                                                                "header": first["header"], "profile": first["profile"]})
    assert status == 400 and body["error"]["message"] == "Ollama couldn't build it: Error: unknown parameter"
    assert built[-1][0] == "rm", "its check model is removed even so"
    assert user_models.read("llama3.1", models).revision == first["revision"]


def test_a_reset_returns_to_nuru_s_settings_and_a_stale_one_is_refused(admin):
    client, built, models, *_ = admin
    status, qwen, _ = client.call("PUT", "/v1/models/settings", {
        "model": "qwen3.5:4b", "expected_revision": "initial", "header": "FROM __BASE_MODEL__\n", "profile": 'think: {"off": false, low: true, high: true}\ndefault: "off"\n'})
    assert status == 200 and qwen["custom"] and qwen["default"]
    status, body, _ = client.call("DELETE", "/v1/models/settings?model=qwen3.5:4b&expected_revision=initial")
    assert status == 409
    status, reset, _ = client.call("DELETE", f"/v1/models/settings?model=qwen3.5:4b&expected_revision={qwen['revision']}")
    assert status == 200 and not reset["custom"] and reset["header"] == reset["nuru"]["header"], \
        "the default model's family can go back to Nuru's own"
    assert reset["revision"] not in ("initial", qwen["revision"]), "a reset is a revision of its own"


def test_the_default_model_s_family_can_t_be_reset_away_when_nuru_has_none_of_its_own(admin):
    client, built, models, default, *_ = admin
    added = client.call("POST", "/v1/models", {"model": "llama3.1:8b"})[1]
    default["id"] = "llama3.1:8b"
    status, body, _ = client.call("DELETE", f"/v1/models/settings?model=llama3.1:8b&expected_revision={added['revision']}")
    assert status == 409 and "choose another default model first" in body["error"]["message"]
    for other in ("anthropic:claude-haiku-5-5", "Not A Model Name"):  # Claude; a setting a hand slipped on
        default["id"] = other
        assert get(client, "llama3.1:8b")[1]["default"] is False
    status, reset, _ = client.call("DELETE", f"/v1/models/settings?model=llama3.1:8b&expected_revision={added['revision']}")
    assert status == 200 and reset["header"] is None and not reset["custom"], "not offered after a restart"


@pytest.mark.parametrize("path", [
    "/v1/models/settings", "/v1/models/settings?model=a&model=b", "/v1/models/settings?model=Nope",
], ids=["missing", "twice", "bad-name"])
def test_a_settings_query_must_name_one_model(admin, path):
    client, *_ = admin
    status, body, _ = client.call("GET", path)
    assert status == 400 and body["error"]["code"] == "invalid_request"


def test_a_body_that_isn_t_an_object_is_refused(admin):
    client, *_ = admin
    assert client.call("POST", "/v1/models", raw=b"[1]", headers={"Content-Type": "application/json"})[0] == 400
    assert client.call("POST", "/v1/models", raw=b"{nope", headers={"Content-Type": "application/json"})[0] == 400


def test_ollama_not_answering_is_unavailable(admin, monkeypatch):
    client, *_ = admin
    monkeypatch.setattr(ollama, "BASE", "http://127.0.0.1:9")
    status, body, _ = client.call("POST", "/v1/models", {"model": "llama3.1:8b"})
    assert status == 503 and body["error"]["code"] == "model_unavailable"
    assert model_settings.ModelSettings(None)._check("FROM x\n") == "Ollama isn't answering"


def test_ollama_failing_otherwise_is_unavailable_too():
    import requests
    def broken(model):
        response = requests.Response()
        response.status_code = 500
        raise requests.HTTPError(response=response)
    settings = model_settings.ModelSettings(None, show=broken)
    with pytest.raises(model_settings.ProtocolError) as refused:
        settings.add({"model": "llama3.1:8b"})
    assert refused.value.code == "model_unavailable"


def test_this_computer_s_client_needs_its_own_token_and_the_preflight_allows_put_and_delete(admin):
    client, *_ = admin
    assert client.call("GET", "/v1/models/settings?model=qwen3.5:4b", token="not-a-token")[0] == 401
    status, _, response = client.call("OPTIONS", "/v1/models/settings", token=None,
                                      headers={"Origin": "app://obsidian.md", "Access-Control-Request-Method": "PUT"})
    assert status == 204 and response.getheader("Access-Control-Allow-Methods") == "GET, POST, PUT, DELETE"


@pytest.mark.skipif(sys.platform == "win32", reason="remote access waits for a later release on Windows")
@pytest.mark.parametrize("method, path", [
    ("GET", "/v1/models/settings/pending"),
    ("GET", "/v1/models/settings?model=qwen3.5:4b"), ("PUT", "/v1/models/settings"),
    ("DELETE", "/v1/models/settings?model=qwen3.5:4b&expected_revision=initial"), ("POST", "/v1/models"),
])
def test_no_remote_device_can_reach_model_settings_whatever_its_credentials(admin, tmp_path, method, path):
    from core.service.remote_credentials import RemoteCredentials, csrf
    from test_remote_server import HOST, ORIGIN, pair
    client, built, models, _, _, (service, local_credentials) = admin
    credentials = RemoteCredentials(tmp_path / "remote")
    server = NuruServer(service, local_credentials, 0, remote_origin=ORIGIN, remote_credentials=credentials, keepalive_seconds=0.1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        remote = Client(server.server_port, "")
        cookie, _ = pair(remote, credentials, scopes=("chat:use", "notes:read", "notes:write"))
        headers = {"Host": HOST, "Origin": ORIGIN, "Content-Type": "application/json", "X-Nuru-CSRF": csrf(cookie),
                   "Cookie": f"nuru_session={cookie}"}
        status, body, _ = remote.call(method, path, {"model": "llama3.1:8b"}, token=None, headers=headers)
        assert status == 404 and built == [] and not models.exists()
        status, _, _ = remote.call(method, path, {"model": "llama3.1:8b"}, token=client.token, headers=headers)
        assert status == 404, "this computer's own token over the remote listener"
        shortcut = credentials.issue("shortcut", "shortcut", {"capture:create"})
        assert client.call(method, path, {"model": "llama3.1:8b"}, token=shortcut)[0] == 401, \
            "a remote device's token on this computer's listener"
    finally:
        server.closing = True
        server.shutdown()
        server.server_close()
        thread.join(2)
        credentials.close()


@pytest.mark.parametrize("model", ["qwen3.5:4b", "llama3.1:8b"])
def test_successful_add_invalidates_choices_even_without_a_settings_write(admin, monkeypatch, model):
    from core import main_models
    client, *_ = admin
    monkeypatch.setattr(main_models, "_cache", (0, []))
    assert client.call("POST", "/v1/models", {"model": model})[0] == 200
    assert main_models._cache is None


def test_failed_add_preserves_choices_cache(admin, monkeypatch):
    from core import main_models
    client, *_ = admin
    cached = (0, [])
    monkeypatch.setattr(main_models, "_cache", cached)
    assert client.call("POST", "/v1/models", {"model": "gemma4:e2b"})[0] == 400
    assert main_models._cache is cached


def test_pending_administration_groups_tags_and_survives_settings_reopening(admin):
    client, _, _, _, _, _ = admin
    assert client.call("POST", "/v1/models", {"model": "llama3.1:8b"})[0] == 200
    first = client.call("GET", "/v1/models/settings/pending")[1]
    second = client.call("GET", "/v1/models/settings/pending")[1]
    assert first == second
    [row] = first["pending"]
    assert row["family"] == "llama3.1"
    assert row["models"] == ["llama3.1:70b", "llama3.1:8b"]
    assert row["revision"] != row["active_revision"]


@pytest.mark.parametrize("unreachable", [False, True])
def test_pending_family_remains_without_installed_tags_or_ollama(admin, unreachable):
    import requests
    client, _, directory, *_ = admin
    assert client.call("POST", "/v1/models", {"model": "llama3.1:8b"})[0] == 200
    def names():
        if unreachable:
            raise requests.ConnectionError("Ollama is down")
        return []
    settings = model_settings.ModelSettings(None, directory=directory, names=names)
    [row] = settings.administration()["pending"]
    assert row["family"] == "llama3.1" and row["models"] == []
    assert row["revision"] != row["active_revision"]


def test_pending_listing_clears_after_process_snapshot_advances_and_ignores_bad_tags(admin, monkeypatch):
    client, _, directory, *_ = admin
    assert client.call("POST", "/v1/models", {"model": "llama3.1:8b"})[0] == 200
    settings = model_settings.ModelSettings(None, directory=directory, names=lambda: ["bad tag", "llama3.1:8b"])
    assert settings.administration()["pending"][0]["models"] == ["llama3.1:8b"]
    monkeypatch.setattr(user_models, "_active", user_models.snapshot(directory))
    settings.names = lambda: pytest.fail("no pending families: no Ollama I/O needed")
    assert settings.administration() == {"pending": []}


@pytest.mark.parametrize("operation", ["save", "reset"])
def test_successful_setting_change_invalidates_choices_and_failures_preserve_it(admin, monkeypatch, operation):
    from core import main_models
    client, *_ = admin
    body = get(client, "qwen3.5:4b")[1]
    cached = (0, [])
    monkeypatch.setattr(main_models, "_cache", cached)
    if operation == "save":
        request = {"model": body["model"], "expected_revision": body["revision"], "header": body["header"], "profile": body["profile"]}
        assert client.call("PUT", "/v1/models/settings", request)[0] == 200
    else:
        assert client.call("DELETE", "/v1/models/settings?model=qwen3.5:4b&expected_revision=initial")[0] == 200
    assert main_models._cache is None
    main_models._cache = cached
    # The original revision is now stale; a refused write must leave the current cache alone.
    if operation == "save":
        assert client.call("PUT", "/v1/models/settings", request)[0] == 409
    else:
        assert client.call("DELETE", "/v1/models/settings?model=qwen3.5:4b&expected_revision=initial")[0] == 409
    assert main_models._cache is cached


def test_failed_build_never_invalidates_or_publishes_a_new_family(admin, monkeypatch):
    import subprocess
    from core import main_models
    client, _, directory, _, failing, _ = admin
    cached = (0, [])
    monkeypatch.setattr(main_models, "_cache", cached)
    failing["error"] = subprocess.CalledProcessError(1, ["ollama"], stderr="build refused")
    assert client.call("POST", "/v1/models", {"model": "llama3.1:8b"})[0] == 400
    assert main_models._cache is cached
    assert user_models.snapshot(directory) == {}


def test_saved_edit_of_an_active_family_stays_pending_with_its_active_revision(admin, monkeypatch):
    client, _, directory, *_ = admin
    added = client.call("POST", "/v1/models", {"model": "llama3.1:8b"})[1]
    monkeypatch.setattr(user_models, "_active", user_models.snapshot(directory))
    saved = client.call("PUT", "/v1/models/settings", {"model": added["model"], "expected_revision": added["revision"],
        "header": added["header"] + "PARAMETER temperature 0.4\n", "profile": added["profile"]})[1]
    [row] = client.call("GET", "/v1/models/settings/pending")[1]["pending"]
    assert row["family"] == "llama3.1" and row["revision"] == saved["revision"]
    assert row["active_revision"] == added["revision"] and row["removed"] is False
    assert user_models.active()["llama3.1"].revision == added["revision"], "the running snapshot is unchanged"
    reset = client.call("DELETE", "/v1/models/settings?model=llama3.1:8b&expected_revision=" + saved["revision"])[1]
    [row] = client.call("GET", "/v1/models/settings/pending")[1]["pending"]
    assert row["revision"] == reset["revision"] != "initial", "reset writes a fresh tombstone revision"
    assert row["removed"] is True and row["active_revision"] == added["revision"]


def test_reset_to_builtin_settings_is_pending_but_does_not_remove_the_family(admin, monkeypatch):
    client, _, directory, *_ = admin
    initial = get(client, "qwen3.5:4b")[1]
    saved = client.call("PUT", "/v1/models/settings", {"model": initial["model"], "expected_revision": initial["revision"],
        "header": initial["header"], "profile": initial["profile"]})[1]
    monkeypatch.setattr(user_models, "_active", user_models.snapshot(directory))
    assert client.call("DELETE", "/v1/models/settings?model=qwen3.5:4b&expected_revision=" + saved["revision"])[0] == 200
    [row] = client.call("GET", "/v1/models/settings/pending")[1]["pending"]
    assert row["family"] == "qwen3.5" and row["removed"] is False
