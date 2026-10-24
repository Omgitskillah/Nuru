"""nuru-serve --managed, as the Obsidian plugin starts it: it stops when the pipe on its stdin closes, and
POST /v1/service/stop stops it with the key in managed.json, but only when it's idle. A service started
any other way has no key, so no plugin can stop it."""

import json
import os
import stat
import threading

import pytest

from core.conversation_store import ConversationStore
from core.service.credentials import Credentials
from core.service.host import Service
from core.service import server as nuru_serve
from core.service.server import NuruServer
from core.service.store import ServiceStore
from core.service.protocol import ProtocolError
from test_service_host import Harness, ScriptedSession, held, reply
from test_service_server import Client


@pytest.fixture
def running(tmp_path):
    credentials = Credentials(tmp_path / "service")
    token = credentials.add("obsidian")
    (tmp_path / "vault").mkdir()
    service = Service(ServiceStore(tmp_path / "service.db"), ConversationStore(tmp_path / "conversation.db"),
                      lambda conversation_id: ScriptedSession([reply("hi")]), tmp_path / "vault")
    server = NuruServer(service, credentials, port=0, keepalive_seconds=0.2)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, Client(server.server_port, token), thread, tmp_path / "service" / "managed.json"
    server.closing = True
    service.close()
    server.shutdown()
    server.server_close()
    service.store.close()
    service.conversations.close()


def manage(server, path):
    read, write = os.pipe()
    stdin = os.fdopen(read, "rb")
    nuru_serve._manage(server, path, stdin)
    return os.fdopen(write, "wb")


def test_a_service_not_started_by_the_plugin_cant_be_stopped(running):
    server, client, thread, _ = running
    status, body, _ = client.call("POST", "/v1/service/stop", {"stop_key": "anything"})
    assert status == 404 and thread.is_alive()


def test_managed_json_is_private_and_only_its_key_stops_an_idle_service(running):
    server, client, thread, path = running
    pipe = manage(server, path)
    saved = json.loads(path.read_text())
    assert saved == {"pid": os.getpid(), "port": server.server_port, "stop_key": server.stop_key}
    assert os.name == "nt" or stat.S_IMODE(path.stat().st_mode) == 0o600

    assert client.call("POST", "/v1/service/stop", {"stop_key": "wrong"})[0] == 403
    assert client.call("POST", "/v1/service/stop", {"stop_key": saved["stop_key"]}, token="other")[0] == 401
    assert thread.is_alive()
    status, body, _ = client.call("POST", "/v1/service/stop", {"stop_key": saved["stop_key"]})
    assert (status, body) == (202, {"stopping": True})
    thread.join(5)
    assert not thread.is_alive(), "serve_forever ended"
    pipe.close()


def test_a_busy_service_refuses_to_stop_and_keeps_its_turn(tmp_path):
    gate = threading.Event()
    h = Harness(tmp_path, [held(gate, "working")])
    turn = h.submit(h.conversation())[1]["turn_id"]
    assert not h.service.close_if_idle()
    gate.set()
    assert h.finished(turn)[-1]["type"] == "completed", "the turn wasn't cancelled"
    assert h.service.close_if_idle(), "idle now"
    h.close()


def test_once_stopping_is_agreed_no_turn_is_accepted(tmp_path):
    h = Harness(tmp_path, [reply("never")])
    conversation = h.conversation()
    assert h.service.close_if_idle()
    with pytest.raises(ProtocolError, match="shutting down"):
        h.submit(conversation)
    h.close()


def test_the_stop_request_answers_409_while_busy(running, monkeypatch):
    server, client, thread, path = running
    pipe = manage(server, path)
    monkeypatch.setattr(server.service, "close_if_idle", lambda: False)
    code, body, _ = client.call("POST", "/v1/service/stop", {"stop_key": server.stop_key})
    assert code == 409 and body["error"]["code"] == "conflict"
    assert thread.is_alive()
    pipe.close()


def test_the_service_stops_when_the_plugin_that_started_it_is_gone(running):
    server, client, thread, path = running
    pipe = manage(server, path)
    assert thread.is_alive()
    pipe.close()  # Obsidian quit, or crashed
    thread.join(5)
    assert not thread.is_alive()


def test_only_its_own_managed_json_is_removed(tmp_path):
    path = tmp_path / "managed.json"
    path.write_text(json.dumps({"pid": os.getpid() + 1}))
    nuru_serve._forget_managed(path)
    assert path.exists(), "a newer service's"
    path.write_text(json.dumps({"pid": os.getpid()}))
    nuru_serve._forget_managed(path)
    assert not path.exists()
