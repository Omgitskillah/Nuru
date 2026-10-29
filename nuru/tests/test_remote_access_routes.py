"""F13: the remote listener's authorization edges and public routes, and host-only device management,
over real loopback HTTP (core.service.remote_access)."""
import sys
import threading

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="remote access waits for a later release on Windows (phone capture there is off)")

from core.service.credentials import Credentials
from core.service.remote_credentials import COOKIE, csrf
from core.service.server import NuruServer
from test_remote_server import HOST, ORIGIN, call, pair, remote  # noqa: F401
from test_service_server import Client, served  # noqa: F401


@pytest.fixture
def control(remote):
    """The local listener that manages remote devices, sharing the remote fixture's registry."""
    client, credentials, local, service, _ = remote
    local_credentials = Credentials(service.vault.parent / "control")
    server = NuruServer(service, local_credentials, 0, remote_credentials=credentials)
    server.remote_credentials_origin = ORIGIN
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Client(server.server_port, local_credentials.add("host")), client, credentials
    server.shutdown()
    server.server_close()
    thread.join(2)


def devices(control, payload):
    return control.call("POST", "/v1/devices", payload)


# ---------------------------------------------------------------- who may use the remote listener
def test_a_cookie_that_cannot_be_parsed_is_unauthorized(remote):
    client, credentials, *_ = remote
    pair(client, credentials)
    status, body, _ = client.call("GET", "/v1/status", token=None,
                                  headers={"Host": HOST, "Cookie": f'{COOKIE}="a\x01b"'})
    assert (status, body) == (401, {"ok": False, "error": "unauthorized"})


@pytest.mark.parametrize("headers", [
    {"Authorization": "Bearer x", "Cookie": "a=b"},  # both kinds at once
    {"Authorization": "Basic eDp5"},  # a kind it doesn't take
])
def test_a_request_with_mixed_or_unknown_credentials_is_unauthorized(remote, headers):
    client, *_ = remote
    status, body, response = client.call("GET", "/v1/status", token=None, headers={"Host": HOST, **headers})
    assert (status, body) == (401, {"ok": False, "error": "unauthorized"})


def test_two_cookie_headers_are_unauthorized_even_if_one_is_valid(remote):
    client, credentials, *_ = remote
    cookie, _ = pair(client, credentials)
    connection = __import__("http.client").client.HTTPConnection("127.0.0.1", client.port, timeout=10)
    connection.putrequest("GET", "/v1/status", skip_host=True)
    connection.putheader("Host", HOST)
    connection.putheader("Cookie", f"{COOKIE}={cookie}")
    connection.putheader("Cookie", "other=1")
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 401
    connection.close()


def test_a_device_over_its_rate_limit_is_told_to_wait(remote, monkeypatch):
    client, credentials, *_ = remote
    cookie, _ = pair(client, credentials)
    monkeypatch.setattr(credentials, "admit", lambda device: False)
    status, body, response = call(client, "GET", "/v1/status", cookie=cookie)
    assert (status, body) == (429, {"ok": False, "error": "rate_limited"})
    assert response.getheader("Retry-After") == "60"


# ---------------------------------------------------------------- public onboarding routes
@pytest.mark.parametrize("path, media, marker", [("/", "text/html; charset=utf-8", b"<"),
                                                 ("/pair.js", "text/javascript; charset=utf-8", b"/v1/pair")])
def test_the_pairing_page_and_its_script_are_served_without_credentials(remote, path, media, marker):
    client, *_ = remote
    status, body, response = call(client, "GET", path)
    assert status == 200 and response.getheader("Content-Type") == media and marker in body


def test_pairing_takes_only_a_post_from_the_configured_origin(remote):
    client, credentials, *_ = remote
    code = credentials.issue("phone", "browser", ["chat:use"])
    assert call(client, "GET", "/v1/pair")[:2] == (405, {"ok": False, "error": "method_not_allowed"})
    foreign = {"Origin": "https://evil.tail.ts.net", "Authorization": "Pairing " + code}
    assert call(client, "POST", "/v1/pair", extra=foreign)[0] == 403  # the listener's own origin check
    missing = {"Authorization": "Pairing " + code}  # no Origin: pairing's own check
    assert call(client, "POST", "/v1/pair", extra=missing)[:2] == (403, {"ok": False, "error": "forbidden"})
    assert credentials.redeem(code)  # neither refused attempt used up the code


# ---------------------------------------------------------------- host-only device management
def test_device_management_says_so_when_remote_access_is_off(remote):
    _, _, local, *_ = remote  # the plain local listener: no remote registry
    status, body, _ = local.call("POST", "/v1/devices", {"action": "list"})
    assert status == 404 and body["error"]["code"] == "not_found"


@pytest.mark.parametrize("payload, message", [
    (["list"], "invalid device operation"),
    ({"action": "list", "extra": 1}, "invalid device operation"),
    ({"action": "pair"}, "device name required"),
    ({"action": "pair", "name": "phone", "scopes": "chat:use"}, "explicit scope list required"),
    ({"action": "pair", "name": "phone", "scopes": ["chat:use", 3]}, "explicit scope list required"),
    ({"action": "explode", "name": "phone"}, "unknown device action"),
])
def test_a_malformed_device_operation_is_refused(control, payload, message):
    control, _, credentials = control
    status, body, _ = devices(control, payload)
    assert status == 400 and body["error"] == {"code": "invalid_request", "message": message}
    assert credentials.list() == []


def test_the_host_lists_pairs_and_cancels_a_device(control):
    control, client, credentials = control
    status, body, _ = devices(control, {"action": "pair", "name": "phone", "scopes": ["chat:use", "notes:read"]})
    assert status == 200 and body["expires_in"] == 300 and body["origin"] == ORIGIN
    assert "7 days absolute" in body["session_lifetime"]
    assert [d["name"] for d in devices(control, {"action": "list"})[1]["devices"]] == ["phone"]

    assert devices(control, {"action": "cancel", "name": "phone"})[1] == {"ok": True}
    refused = call(client, "POST", "/v1/pair", extra={"Origin": ORIGIN, "Authorization": "Pairing " + body["code"]})
    assert refused[0] == 401  # a cancelled code can't be redeemed


def test_a_shortcut_issued_by_the_host_captures_and_a_revoke_stops_it(control):
    control, client, credentials = control
    status, body, _ = devices(control, {"action": "shortcut", "name": "shortcut"})
    assert status == 200 and body["token"] and body["name"] == "shortcut"
    assert credentials.authenticate(body["token"], "shortcut").scopes == {"capture:create"}

    assert devices(control, {"action": "revoke", "name": "shortcut"})[1] == {"revoked": True}
    assert call(client, "POST", "/v1/captures", {"content": "not saved"}, token=body["token"])[0] == 401


def test_an_android_setup_link_downloads_once(control):
    control, client, credentials = control
    status, body, _ = devices(control, {"action": "android", "name": "tablet"})
    assert status == 200 and body["url"].startswith(ORIGIN + "/v1/android-setup/") and body["expires_in"] == 300
    path = body["url"][len(ORIGIN):]
    first = call(client, "GET", path)
    assert first[0] == 200 and "remote-notes.json" in first[2].getheader("Content-Disposition")
    assert call(client, "GET", path)[0] == 404
