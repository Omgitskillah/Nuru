"""Real loopback HTTP, disposable vault and fake providers only. No live model."""
import http.client
import json
import socket
import threading

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="remote access waits for a later release on Windows (phone capture there is off)")

from core.service.remote_credentials import COOKIE, RemoteCredentials, csrf
from core.service.server import NuruServer
from test_service_server import Client, served  # noqa: F401

ORIGIN = "https://host.tail.ts.net"
HOST = "host.tail.ts.net"


@pytest.fixture
def remote(served, tmp_path):
    local, service, local_credentials, built = served
    credentials = RemoteCredentials(tmp_path / "remote")
    server = NuruServer(service, local_credentials, 0, remote_origin=ORIGIN, remote_credentials=credentials,
                        keepalive_seconds=0.1, connections=8)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Local control plane shares the remote registry but is never remotely routed.
    # Retrieve the local server is unnecessary: control route has its own test below.
    yield Client(server.server_port, ""), credentials, local, service, built
    server.closing = True
    server.shutdown()
    server.server_close()
    thread.join(2)
    credentials.close()


def call(client, method, path, body=None, *, token=None, cookie=None, extra=None):
    headers = {"Host": HOST, "Content-Type": "application/json", **(extra or {})}
    if cookie:
        headers["Cookie"] = f"{COOKIE}={cookie}"
    return client.call(method, path, body, token=token, headers=headers)


def pair(client, credentials, name="phone", scopes=("chat:use", "notes:read")):
    code = credentials.issue(name, "browser", scopes)
    status, body, response = call(client, "POST", "/v1/pair", extra={"Origin": ORIGIN, "Authorization": "Pairing " + code})
    assert status == 200
    cookie = response.getheader("Set-Cookie")
    assert all(flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=Strict", "Path=/"))
    token = cookie.split(";", 1)[0].split("=", 1)[1]
    assert body["csrf"] == csrf(token)
    return token, code


def test_listeners_do_not_accept_each_others_credentials(remote):
    client, credentials, local, service, built = remote
    token = credentials.issue("shortcut", "shortcut", {"capture:create"})
    assert call(client, "POST", "/v1/captures", {"content": "hello"}, token=local.token)[0] == 401
    assert local.call("POST", "/v1/captures", {"content": "hello"}, token=token)[0] == 401
    assert call(client, "GET", "/v1/status", token=local.token, extra={
        "X-Forwarded-Host": "localhost", "X-Forwarded-For": "127.0.0.1", "X-Forwarded-Proto": "https"})[0] == 401
    assert not built


@pytest.mark.parametrize("path", ["/v1/status", "/v1/conversations", "/v1/turns/abcdefgh/events",
                                  "/v1/requests/phone-request-0001", "/v1/devices", "/v1/notes/anything"])
def test_capture_only_cannot_reach_service_or_vault(remote, monkeypatch, path):
    client, credentials, _, service, built = remote
    token = credentials.issue("shortcut", "shortcut", {"capture:create"})
    def forbidden(*args, **kwargs):
        pytest.fail("unauthorized request reached service")
    monkeypatch.setattr(service, "status", forbidden)
    monkeypatch.setattr(service, "list_conversations", forbidden)
    monkeypatch.setattr(service.captures, "create", forbidden)
    assert call(client, "GET", path, token=token)[0] == 403
    assert not built


def test_invalid_and_revoked_refused_before_body_is_consumed(remote, monkeypatch):
    client, credentials, _, service, built = remote
    token = credentials.issue("shortcut", "shortcut", {"capture:create"})
    credentials.revoke("shortcut")
    monkeypatch.setattr(service.captures, "create", lambda *a: pytest.fail("writer called"))
    for secret in ("wrong", token):
        connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=2)
        connection.putrequest("POST", "/v1/captures", skip_host=True)
        connection.putheader("Host", HOST)
        connection.putheader("Authorization", "Bearer " + secret)
        connection.putheader("Content-Length", "100")
        connection.endheaders()  # deliberately never send the declared body
        assert connection.getresponse().status == 401
        connection.close()
    assert not built


def test_cookie_origin_csrf_logout_and_replay(remote):
    client, credentials, _, _, _ = remote
    cookie, code = pair(client, credentials)
    assert call(client, "POST", "/v1/pair", extra={"Origin": ORIGIN, "Authorization": "Pairing " + code})[0] == 401
    assert call(client, "GET", "/v1/session", cookie=cookie)[1]["csrf"] == csrf(cookie)
    payload = {"request_id": "phone-conversation-01"}
    for headers in ({}, {"Origin": ORIGIN}, {"Origin": "https://evil.test", "X-Nuru-CSRF": csrf(cookie)},
                    {"Origin": ORIGIN, "X-Nuru-CSRF": "wrong"}):
        assert call(client, "POST", "/v1/conversations", payload, cookie=cookie, extra=headers)[0] == 403
    headers = {"Origin": ORIGIN, "X-Nuru-CSRF": csrf(cookie)}
    assert call(client, "POST", "/v1/conversations", payload, cookie=cookie, extra=headers)[0] == 201
    assert "vault" not in call(client, "GET", "/v1/status", cookie=cookie)[1]
    assert call(client, "POST", "/v1/logout", cookie=cookie, extra=headers)[0] == 200
    assert call(client, "GET", "/v1/session", cookie=cookie)[0] == 401


def test_scope_without_chat_can_still_logout(remote):
    client, credentials, *_ = remote
    cookie, _ = pair(client, credentials, scopes=["capture:create"])
    assert call(client, "GET", "/v1/session", cookie=cookie)[0] == 200
    assert call(client, "GET", "/v1/conversations", cookie=cookie)[0] == 403
    assert call(client, "POST", "/v1/logout", cookie=cookie, extra={"Origin": ORIGIN, "X-Nuru-CSRF": csrf(cookie)})[0] == 200


def test_rotation_and_expiry_invalidate_http_sessions(remote):
    client, credentials, *_ = remote
    cookie, _ = pair(client, credentials)
    credentials.issue("phone", "browser", ["chat:use"])
    assert call(client, "GET", "/v1/session", cookie=cookie)[0] == 401
    cookie, _ = pair(client, credentials)
    credentials.clock = lambda: 10**12
    assert call(client, "GET", "/v1/session", cookie=cookie)[0] == 401


def test_setup_fixture_headers_and_single_download(remote):
    client, credentials, *_ = remote
    path = credentials.setup("android", ORIGIN)
    assert call(client, "POST", path)[0] == 405
    status, body, response = call(client, "GET", path)
    assert status == 200 and body["version"] == 90
    assert response.getheader("Cache-Control") == "no-store"
    assert response.getheader("Referrer-Policy") == "no-referrer"
    assert response.getheader("Content-Disposition") == 'attachment; filename="remote-notes.json"'
    shortcut = body["categories"][0]["shortcuts"][0]
    assert shortcut["url"] == ORIGIN + "/v1/captures"
    assert shortcut["bodyContent"] == "{{payload}}"
    assert "codeOnPrepare" in shortcut and "codeOnSuccess" in shortcut
    assert call(client, "GET", path)[0] == 404
    assert call(client, "POST", "/v1/captures", {"content": "from Android"}, token=shortcut["authToken"])[0] == 201


def test_rotation_preserves_capture_receipts_and_legacy_import(remote):
    client, credentials, _, service, built = remote
    token = credentials.issue("shortcut", "shortcut", {"capture:create"}, legacy="ab" * 32)
    payload = {"request_id": "remote-capture-0001", "content": "text\n雪"}
    receipt = call(client, "POST", "/v1/captures", payload, token=token)[1]
    new = credentials.issue("shortcut", "shortcut", {"capture:create"})
    assert call(client, "POST", "/v1/captures", payload, token=token)[0] == 401
    assert call(client, "POST", "/v1/captures", payload, token=new)[:2] == (201, receipt)
    other = credentials.issue("other", "shortcut", {"capture:create"})
    assert call(client, "GET", "/v1/captures/requests/remote-capture-0001", token=other)[0] == 404
    assert len(list((service.vault / "Inbox").glob("*.md"))) == 1
    assert built == []


def test_wrong_host_and_origin_cannot_be_overridden_by_proxy_headers(remote):
    client, credentials, *_ = remote
    token = credentials.issue("shortcut", "shortcut", {"capture:create"})
    for headers in ({"Host": "evil.test", "X-Forwarded-Host": HOST},
                    {"Origin": "https://evil.test", "X-Forwarded-Proto": "https"}):
        assert call(client, "POST", "/v1/captures", {"content": "no"}, token=token, extra=headers)[0] == 403


def test_host_management_is_local_only(remote):
    client, credentials, local, service, _ = remote
    # Build a separate local control listener sharing the fixture's local credential.
    from core.service.credentials import Credentials
    # Obtain a fresh local credential directory without touching the fixture's one.
    control_credentials = Credentials(service.vault.parent / "control")
    local_token = control_credentials.add("host")
    server = NuruServer(service, control_credentials, 0, remote_credentials=credentials)
    server.remote_credentials_origin = ORIGIN
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        control = Client(server.server_port, local_token)
        status, body, _ = control.call("POST", "/v1/devices", {"action": "pair", "name": "approved", "scopes": ["chat:use"]})
        assert status == 200 and credentials.redeem(body["code"])
        cookie, _ = pair(client, credentials)
        assert call(client, "POST", "/v1/devices", {"action": "pair", "name": "no", "scopes": ["notes:write"]},
                    cookie=cookie, extra={"Origin": ORIGIN, "X-Nuru-CSRF": csrf(cookie)})[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_socket_capacity_is_bounded_without_spawning_unlimited_threads(remote):
    client, credentials, *_ = remote
    sockets = []
    try:
        for _ in range(12):
            sock = socket.create_connection(("127.0.0.1", client.port), timeout=2)
            sock.sendall(b"GET / HTTP/1.1\r\n")
            sockets.append(sock)
        # The eight admitted clients time out; excess clients close immediately.
        try:
            assert sockets[-1].recv(1) == b""
        except ConnectionResetError:
            pass  # an immediate TCP reset is also a bounded refusal
    finally:
        for sock in sockets:
            sock.close()


def test_revocation_during_upload_prevents_capture(remote):
    client, credentials, _, service, built = remote
    token = credentials.issue("shortcut", "shortcut", {"capture:create"})
    data = json.dumps({"content": "do not save"}).encode()
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=3)
    connection.putrequest("POST", "/v1/captures", skip_host=True)
    connection.putheader("Host", HOST)
    connection.putheader("Authorization", "Bearer " + token)
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Content-Length", str(len(data)))
    connection.endheaders()
    # Revocation either wins initial authentication or the post-upload recheck.
    credentials.revoke("shortcut")
    connection.send(data)
    response = connection.getresponse()
    assert response.status == 401
    assert json.loads(response.read()) == {"ok": False, "error": "unauthorized"}
    connection.close()
    assert not list((service.vault / "Inbox").glob("*.md")) and not built


def test_cutover_rollback_and_forward_again_preserve_notes_and_receipts(remote):
    """Disposable HTTP proxy rehearsal, not a claim of a real Tailscale cutover.

    The old endpoint is represented by a frozen legacy responder. Rollback changes
    only its proxy target, leaving the Nuru databases and authored bytes intact.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    client, credentials, _, service, built = remote
    legacy_token = "cd" * 32
    token = credentials.issue("legacy", "shortcut", {"capture:create"}, legacy=legacy_token)
    class Legacy(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            assert self.headers["Authorization"] == "Bearer " + legacy_token
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(201)
            self.end_headers()
            self.wfile.write(b'{"ok":true,"legacy":true}')
    legacy = ThreadingHTTPServer(("127.0.0.1", 0), Legacy)
    target = [legacy.server_port]
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            connection = http.client.HTTPConnection("127.0.0.1", target[0], timeout=3)
            connection.request("POST", self.path, self.rfile.read(int(self.headers["Content-Length"])),
                               {"Host": HOST, "Authorization": self.headers["Authorization"], "Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read()
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            connection.close()
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    for server in (legacy, proxy):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    routed = Client(proxy.server_port, token)
    try:
        assert routed.call("POST", "/v1/captures", {"content": "old"})[1]["legacy"]
        target[0] = client.port  # after old accepted request above is complete
        assert routed.call("POST", "/v1/captures", {"content": "legacy after cutover"})[0] == 201
        payload = {"request_id": "cutover-request-0001", "content": "preserve me"}
        receipt = routed.call("POST", "/v1/captures", payload)[1]
        notes = {p: p.read_bytes() for p in (service.vault / "Inbox").glob("*.md")}
        target[0] = legacy.server_port
        assert routed.call("POST", "/v1/captures", {"content": "rollback"})[1]["legacy"]
        target[0] = client.port
        assert routed.call("POST", "/v1/captures", payload)[:2] == (201, receipt)
        assert notes == {p: p.read_bytes() for p in (service.vault / "Inbox").glob("*.md")}
        assert not built
    finally:
        for server in (proxy, legacy):
            server.shutdown()
            server.server_close()


def test_foreign_url_resource_is_rejected_without_waiting_for_body(remote):
    client, credentials, *_ = remote
    cookie, _ = pair(client, credentials)
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=2)
    connection.putrequest("POST", "/v1/conversations/not-found/turns", skip_host=True)
    for key, value in {"Host": HOST, "Cookie": f"{COOKIE}={cookie}", "Origin": ORIGIN,
                       "X-Nuru-CSRF": csrf(cookie), "Content-Length": "100"}.items():
        connection.putheader(key, value)
    connection.endheaders()
    assert connection.getresponse().status == 404
    connection.close()


def test_revoked_queued_turn_never_builds_a_model(remote):
    _, _, _, service, built = remote
    _, conversation = service.create_conversation("operator", {"request_id": "conversation-test-01"})
    _, turn = service.submit_turn("operator", conversation["conversation_id"],
                                  {"request_id": "denied-turn-test-01", "text": "hello"}, authorize=lambda name: False)
    events, ended = service.wait_events(turn["turn_id"], 0, 2)
    cursor = events[-1]["seq"] if events else 0
    if not ended:
        more, ended = service.wait_events(turn["turn_id"], cursor, 2)
        events += more
    assert events[-1]["type"] == "failed"
    assert not built


def test_a_device_revoked_after_admission_never_builds_a_model(remote):
    """Admission doesn't decide: the turn rechecks the device's credential when it starts, so one
    revoked after its HTTP request was authenticated never reaches a model."""
    from core.service.server import remote_tool_allowed
    client, credentials, _, service, built = remote
    cookie, _ = pair(client, credentials, scopes=["chat:use", "notes:read"])
    device = credentials.authenticate(cookie, "browser")
    assert remote_tool_allowed(credentials, device, None)  # admitted: allowed to start a turn
    _, conversation = service.create_conversation("operator", {"request_id": "conversation-test-02"})
    assert credentials.revoke("phone")
    _, turn = service.submit_turn("operator", conversation["conversation_id"],
                                  {"request_id": "revoked-turn-test-01", "text": "hello"},
                                  authorize=lambda name: remote_tool_allowed(credentials, device, name))
    events, ended = [], False
    for _ in range(10):  # each wait returns as soon as there are new events
        more, ended = service.wait_events(turn["turn_id"], events[-1]["seq"] if events else 0, 1)
        events += more
        if ended:
            break
    assert events[-1]["type"] == "failed" and "revoked" in events[-1]["data"]["message"]
    assert not built


def test_write_approval_requires_write_scope_before_body(remote, monkeypatch):
    client, credentials, _, service, _ = remote
    cookie, _ = pair(client, credentials, scopes=["chat:use", "notes:read"])
    monkeypatch.setattr(service, "check_target", lambda *a: None)
    monkeypatch.setattr(service, "answer_question", lambda *a: pytest.fail("approval reached host"))
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=2)
    connection.putrequest("POST", "/v1/questions/question-0001/answers", skip_host=True)
    for key, value in {"Host": HOST, "Cookie": f"{COOKIE}={cookie}", "Origin": ORIGIN,
                       "X-Nuru-CSRF": csrf(cookie), "Content-Length": "100"}.items():
        connection.putheader(key, value)
    connection.endheaders()
    assert connection.getresponse().status == 403
    connection.close()


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS", "PUT", "PATCH", "DELETE"])
def test_setup_wrong_methods_do_not_consume_download(remote, method):
    client, credentials, *_ = remote
    path = credentials.setup("android", ORIGIN)
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=2)
    connection.request(method, path, headers={"Host": HOST})
    response = connection.getresponse()
    assert response.status == 405
    if method == "HEAD":
        assert response.read() == b""
    else:
        assert json.loads(response.read())["error"] == "method_not_allowed"
    connection.close()
    assert call(client, "GET", path)[0] == 200


def test_chat_only_cannot_disclose_or_continue_local_history(remote):
    from test_service_host import ScriptedSession, reply
    client, credentials, local, service, built = remote
    note_excerpt = "Private note excerpt: violet telescope"
    service._factory = lambda _: ScriptedSession([reply(note_excerpt)])
    conversation = local.call("POST", "/v1/conversations", {"request_id": "local-conversation-01"})[1]["conversation_id"]
    turn = local.call("POST", f"/v1/conversations/{conversation}/turns",
                      {"request_id": "local-turn-secret-01", "text": "Read my private note"})[1]
    assert note_excerpt.encode() in local.call("GET", turn["events"])[1]
    cookie, _ = pair(client, credentials, scopes=["chat:use"])
    headers = {"Origin": ORIGIN, "X-Nuru-CSRF": csrf(cookie)}
    paths = ["/v1/conversations", f"/v1/conversations/{conversation}", turn["events"],
             "/v1/requests/local-turn-secret-01?operation=submit_turn"]
    for path in paths:
        status, body, _ = call(client, "GET", path, cookie=cookie)
        assert status == 403 and note_excerpt not in json.dumps(body)
    # In particular, do not wait for a body or reach model dispatch on continuation.
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=2)
    connection.putrequest("POST", f"/v1/conversations/{conversation}/turns", skip_host=True)
    for key, value in {"Host": HOST, "Cookie": f"{COOKIE}={cookie}", "Content-Length": "100", **headers}.items():
        connection.putheader(key, value)
    connection.endheaders()
    assert connection.getresponse().status == 403
    connection.close()
    assert call(client, "POST", "/v1/conversations", {"request_id": "denied-create-0001"}, cookie=cookie, extra=headers)[0] == 403
    assert call(client, "POST", f'/v1/turns/{turn["turn_id"]}/cancel', cookie=cookie, extra=headers)[0] == 403
    assert call(client, "GET", "/v1/status", cookie=cookie)[0] == 200
    assert call(client, "GET", "/v1/session", cookie=cookie)[0] == 200
    # Read-authorized devices retain the intended operator conversation sharing.
    reader, _ = pair(client, credentials, name="reader", scopes=["chat:use", "notes:read"])
    assert note_excerpt in json.dumps(call(client, "GET", f"/v1/conversations/{conversation}", cookie=reader)[1])
    assert call(client, "POST", "/v1/logout", cookie=cookie, extra=headers)[0] == 200


def test_pairing_throttle_counts_missing_credentials_and_ignores_forged_peer_headers(remote):
    from core.service.remote_credentials import PAIR_FAILURE_LIMIT
    client, credentials, *_ = remote
    code = credentials.issue("phone", "browser", ["chat:use", "notes:read"])
    for i in range(PAIR_FAILURE_LIMIT):
        headers = {"Origin": ORIGIN, "X-Forwarded-For": f"100.64.0.{i}"}
        assert call(client, "POST", "/v1/pair", extra=headers)[0] == 401
    status, body, response = call(client, "POST", "/v1/pair", extra={"Origin": ORIGIN, "Authorization": "Pairing " + code})
    assert status == 429 and body["error"] == "pairing_rate_limited"
    assert 1 <= int(response.getheader("Retry-After")) <= 60
    assert response.getheader("Cache-Control") == "no-store"
    assert response.getheader("Set-Cookie") is None


def test_expired_shortcut_has_actionable_401_without_changing_legacy_body(remote):
    client, credentials, *_ = remote
    token = credentials.issue("shortcut", "shortcut", {"capture:create"})
    credentials.clock = lambda: 10**12
    status, body, response = call(client, "POST", "/v1/captures", {"content": "not saved"}, token=token)
    assert status == 401 and body == {"ok": False, "error": "unauthorized"}
    assert "reissue" in response.getheader("WWW-Authenticate")


def test_session_response_explains_idle_and_absolute_expiry(remote):
    client, credentials, *_ = remote
    cookie, _ = pair(client, credentials)
    info = call(client, "GET", "/v1/session", cookie=cookie)[1]
    assert info["expires_at"] and info["idle_expires_at"]
    assert "24 hours idle or 7 days absolute" in info["renewal"]
