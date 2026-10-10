"""nuru-serve over real HTTP on a free loopback port, with a scripted Session: who may call, the routes,
and a stream that is replayed and followed. No model."""

import http.client
import json
import os
import stat
import threading

import pytest

from core.conversation_store import ConversationStore
from core.service import protocol
from core.service.credentials import Credentials
from core.service.host import Service
from core.service.server import NuruServer, main
from core.service.store import ServiceStore
from test_service_host import ScriptedSession, held, reply


@pytest.fixture
def served(tmp_path):
    credentials = Credentials(tmp_path / "service")
    token = credentials.add("obsidian")
    session = ScriptedSession([reply("Hello", " there"), reply("again")])
    built = []

    def factory(conversation_id):
        built.append(conversation_id)
        session.conversation_id = conversation_id
        return session

    (tmp_path / "vault").mkdir()
    service = Service(ServiceStore(tmp_path / "service.db"), ConversationStore(tmp_path / "conversation.db"),
                      factory, tmp_path / "vault")
    server = NuruServer(service, credentials, port=0, keepalive_seconds=0.2)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Client(server.server_port, token), service, credentials, built
    server.closing = True
    service.close()
    server.shutdown()
    server.server_close()


class Client:
    def __init__(self, port, token):
        self.port, self.token = port, token

    def call(self, method, path, body=None, *, token="mine", headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        sent = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        if token:
            sent["Authorization"] = f"Bearer {self.token if token == 'mine' else token}"
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        connection.request(method, path, body=data, headers=sent)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        kind = response.getheader("Content-Type")
        return response.status, (json.loads(payload) if kind == "application/json" else payload), response


def test_refused_callers_reach_nothing(served):
    client, service, credentials, built = served
    other = Credentials(credentials.directory.parent / "elsewhere").add("obsidian")
    create = {"request_id": "req-conversation-0001"}
    refusals = [
        (dict(token=None), 401, "unauthorized"),
        (dict(token="not-the-token"), 401, "unauthorized"),
        (dict(token=other), 401, "unauthorized"),
        (dict(headers={"Origin": "https://evil.example"}), 403, "forbidden_origin"),
        (dict(headers={"Host": "evil.example"}), 403, "forbidden"),
        (dict(headers={"Host": f"127.0.0.1:{client.port + 1}"}), 403, "forbidden"),
    ]
    for options, status, code in refusals:
        got, body, _ = client.call("POST", "/v1/conversations", create, **options)
        assert (got, body["error"]["code"]) == (status, code), options
    assert service.list_conversations("operator", None, 50)["conversations"] == [] and built == []


def test_a_revoked_token_stops_working(served):
    client, service, credentials, _ = served
    assert client.call("GET", "/v1/status")[0] == 200
    credentials.remove("obsidian")
    assert client.call("GET", "/v1/status")[0] == 401


def test_malformed_requests_are_refused_without_echoing_them(served):
    client, service, *_ = served
    cases = [
        ("GET", "/v2/status", None, 400, "unsupported_version"),
        ("POST", "/v1/conversations", b"{not json", 400, "invalid_request"),
        ("POST", "/v1/conversations", json.dumps({"request_id": "short"}).encode(), 400, "invalid_request"),
        ("POST", "/v1/conversations", b"x" * (protocol.MAX_REQUEST_BYTES + 1), 413, "payload_too_large"),
        ("GET", "/v1/turns/../events", None, 404, "not_found"),
        ("GET", "/v1/nothing-here", None, 404, "not_found"),
        ("GET", "/v1/conversations?limit=500", None, 400, "invalid_request"),
        ("GET", "/v1/requests/req-conversation-0001?operation=delete_vault", None, 400, "invalid_request"),
    ]
    for method, path, raw, status, code in cases:
        got, body, _ = client.call(method, path, raw=raw)
        assert (got, body["error"]["code"]) == (status, code), path
        protocol.validate("error", body)
    secret = {"request_id": "req-conversation-0001", "title": "CONFIDENTIAL " * 100}
    got, body, _ = client.call("POST", "/v1/conversations", secret)
    assert got == 400 and "CONFIDENTIAL" not in json.dumps(body)


def test_a_refusal_to_the_plugin_carries_cors_headers(served):
    client, *_ = served
    status, body, response = client.call("GET", "/v1/status", token=None, headers={"Origin": "app://obsidian.md"})
    assert status == 401 and response.getheader("Access-Control-Allow-Origin") == "app://obsidian.md"
    status, _, response = client.call("OPTIONS", "/v1/status", token=None, headers={"Origin": "app://obsidian.md"})
    assert status == 204 and "Authorization" in response.getheader("Access-Control-Allow-Headers")


def test_a_turn_over_http_streams_and_resumes_from_any_cursor(served):
    client, service, *_ = served
    status, body, _ = client.call("GET", "/v1/status")
    protocol.validate("status", body)
    status, created, _ = client.call("POST", "/v1/conversations", {"request_id": "req-conversation-0001", "title": "Trip"})
    assert status == 201
    conversation = created["conversation_id"]
    status, accepted, _ = client.call("POST", f"/v1/conversations/{conversation}/turns",
                                      {"request_id": "req-turn-00000001", "text": "hello"})
    assert status == 202
    protocol.validate("accepted_turn", accepted)
    status, stream, response = client.call("GET", accepted["events"] + "?after=0")
    assert status == 200 and response.getheader("Content-Type") == "text/event-stream"
    events = protocol.parse_sse(stream)
    protocol.check_stream(events)
    assert events[-1]["data"]["reply"] == "Hello there"
    for cursor in range(len(events) + 1):
        assert protocol.parse_sse(client.call("GET", f"{accepted['events']}?after={cursor}")[1]) == events[cursor:]
    status, expired, _ = client.call("GET", f"{accepted['events']}?after={len(events) + 1}")
    assert status == 410 and expired["error"]["resync"] == {"turn": f"/v1/conversations/{conversation}"}

    status, again, _ = client.call("POST", f"/v1/conversations/{conversation}/turns",
                                   {"request_id": "req-turn-00000001", "text": "hello"})
    assert again == {**accepted, "replayed": True}
    status, conflict, _ = client.call("POST", f"/v1/conversations/{conversation}/turns",
                                      {"request_id": "req-turn-00000001", "text": "other"})
    assert (status, conflict["error"]["code"]) == (409, "conflict")
    status, state, _ = client.call("GET", "/v1/requests/req-turn-00000001?operation=submit_turn")
    assert state["state"] == "completed" and state["last_seq"] == len(events)
    status, history, _ = client.call("GET", f"/v1/conversations/{conversation}")
    assert [t["display"]["reply"] for t in history["turns"]] == ["Hello there"] and history["title"] == "Trip"
    status, listed, _ = client.call("GET", "/v1/conversations?limit=10")
    assert [c["id"] for c in listed["conversations"]] == [conversation]
    status, cancelled, _ = client.call("POST", f"/v1/turns/{accepted['turn_id']}/cancel", {"request_id": "req-cancel-0000001"})
    assert status == 202 and service.store.events(accepted["turn_id"]) == events


def test_a_live_stream_follows_the_turn_with_keepalives(served):
    client, service, *_ = served
    gate = threading.Event()
    service._factory = lambda conversation_id: ScriptedSession([held(gate, "first")])
    conversation = client.call("POST", "/v1/conversations", {"request_id": "req-conversation-0001"})[1]["conversation_id"]
    accepted = client.call("POST", f"/v1/conversations/{conversation}/turns", {"request_id": "req-turn-00000001", "text": "hi"})[1]
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=10)
    connection.request("GET", accepted["events"], headers={"Host": f"127.0.0.1:{client.port}",
                                                            "Authorization": f"Bearer {client.token}"})
    response = connection.getresponse()
    seen = b""
    while b"first" not in seen or b": keepalive" not in seen:
        seen += response.read1(65536)
    gate.set()
    seen += response.read()
    connection.close()
    events = protocol.parse_sse(seen)
    protocol.check_stream(events)
    assert events[-1]["data"]["reply"] == "first done"


def test_token_commands_keep_only_a_hash_and_files_private(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NURU_SERVICE_DIR", str(tmp_path / "service"))
    main(["token", "add", "obsidian"])
    directory = tmp_path / "service"
    token = (directory / "clients" / "obsidian.token").read_text()
    assert token not in (directory / "tokens.json").read_text()
    assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700
    for path in (directory / "tokens.json", directory / "clients" / "obsidian.token"):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert Credentials().check(token) == ("operator", "obsidian")
    main(["token", "list"])
    main(["token", "remove", "obsidian"])
    assert Credentials().check(token) is None and not (directory / "clients" / "obsidian.token").exists()
    assert capsys.readouterr().out.splitlines()[1] == "obsidian"
    with pytest.raises(SystemExit):
        main(["token", "add", "../escape"])
