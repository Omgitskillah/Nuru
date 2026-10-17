"""HTTP remote authorization and host-only onboarding operations."""
import hmac
import re
from http.cookies import SimpleCookie, CookieError
from urllib.parse import urlsplit

from core.service.remote_credentials import COOKIE, PairingLimited, csrf
from core.service.protocol import ProtocolError


def https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or parsed.port not in {None, 443} or not re.fullmatch(r"[a-z0-9.-]+\.ts\.net", parsed.hostname)
            or parsed.netloc != parsed.hostname):
        raise ValueError("remote origin must be https://<machine>.<tailnet>.ts.net with no path or port")
    return "https://" + parsed.hostname


def authenticate(handler, method, is_capture):
    credentials = handler.server.remote_credentials
    header = handler.headers.get("Authorization", "")
    cookie_headers = handler.headers.get_all("Cookie", [])
    if header.startswith("Bearer ") and not cookie_headers:
        device = credentials.authenticate(header[7:], "shortcut")
    elif not header and len(cookie_headers) == 1:
        try:
            cookie = SimpleCookie()
            cookie.load(cookie_headers[0])
            token = cookie[COOKIE].value if COOKIE in cookie else ""
        except CookieError:
            token = ""
        device = credentials.authenticate(token, "browser") if token else None
        if device and method != "GET":
            if (handler.headers.get("Origin") != handler.server.remote_origin
                    or not hmac.compare_digest(handler.headers.get("X-Nuru-CSRF", ""), csrf(token))):
                handler.capture_answer(403, {"ok": False, "error": "forbidden"})
                return None
        handler.session_token = token
    else:
        device = None
    if not device:
        if header.startswith("Bearer "):
            handler.extra_headers = {"WWW-Authenticate": 'Bearer error="invalid_token", error_description="Capture credential is invalid, expired or revoked. Ask the host operator to reissue it."'}
        handler.capture_answer(401, {"ok": False, "error": "unauthorized"})
        return None
    path = urlsplit(handler.path).path
    required = {"capture:create"} if is_capture else {"chat:use", "notes:read"}
    # Shared operator history and event/request receipts can contain note text.
    # Require read authority before any of those resources can be accessed or
    # used as model context. Status and session management disclose no history.
    if path == "/v1/status":
        required = {"chat:use"}
    session_route = path in {"/v1/session", "/v1/logout"} and device.kind == "browser"
    if not session_route and not required <= device.scopes:
        handler.capture_answer(403, {"ok": False, "error": "forbidden"})
        return None
    if not credentials.admit(device):
        handler.extra_headers = {"Retry-After": "60"}
        handler.capture_answer(429, {"ok": False, "error": "rate_limited"})
        return None
    handler.device = device
    return device.principal, device.name, device.device_id


def public_route(handler, method, path):
    """Only static onboarding, single-use setup and an approved pairing exchange."""
    credentials = handler.server.remote_credentials
    if path in {"/", "/pair.js"} and method == "GET":
        from pathlib import Path
        filename, media = ("pair.html", "text/html; charset=utf-8") if path == "/" else ("pair.js", "text/javascript; charset=utf-8")
        handler.raw_answer(200, Path(__file__).with_name(filename).read_bytes(), media)
        return True
    match = re.fullmatch(r"/v1/android-setup/([a-f0-9]{64})\.json", path)
    if match:
        data = credentials.download(match[1], consume=method == "GET")
        if data is None:
            handler.capture_answer(404, {"ok": False, "error": "not_found"})
        elif method != "GET":
            handler.capture_answer(405, {"ok": False, "error": "method_not_allowed"})
        else:
            handler.extra_headers = {"Content-Disposition": 'attachment; filename="remote-notes.json"'}
            handler.answer(200, data)
        return True
    if path == "/v1/pair":
        if method != "POST":
            handler.capture_answer(405, {"ok": False, "error": "method_not_allowed"})
        elif handler.headers.get("Origin") != handler.server.remote_origin:
            handler.capture_answer(403, {"ok": False, "error": "forbidden"})
        else:
            # The one-time code is the credential; no unauthenticated body read.
            header = handler.headers.get("Authorization", "")
            try:
                token = credentials.redeem(header[8:] if header.startswith("Pairing ") else "")
            except PairingLimited as error:
                handler.extra_headers = {"Retry-After": str(error.retry_after)}
                handler.capture_answer(429, {"ok": False, "error": "pairing_rate_limited"})
                return True
            if not token:
                handler.capture_answer(401, {"ok": False, "error": "unauthorized"})
            else:
                handler.extra_headers = {"Set-Cookie": f"{COOKIE}={token}; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800"}
                handler.answer(200, {"ok": True, "csrf": csrf(token)})
        return True
    return False


def manage(handler):
    """Authenticated local listener only: this request is explicit host approval."""
    credentials = handler.server.remote_credentials
    if credentials is None:
        raise ProtocolError("not_found", "remote access is not enabled")
    payload = handler.json_body(4096)
    if not isinstance(payload, dict) or set(payload) - {"action", "name", "scopes", "legacy_token"}:
        raise ProtocolError("invalid_request", "invalid device operation")
    action, name = payload.get("action"), payload.get("name")
    if action == "list":
        return handler.answer(200, {"devices": credentials.list()})
    if not isinstance(name, str):
        raise ProtocolError("invalid_request", "device name required")
    try:
        if action == "pair":
            scopes = payload.get("scopes")
            if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
                raise ValueError("explicit scope list required")
            code = credentials.issue(name, "browser", scopes)
            result = {"code": code, "expires_in": 300, "origin": handler.server.remote_credentials_origin,
                      "session_lifetime": "Re-pair after 24 hours idle or 7 days absolute."}
        elif action == "shortcut":
            secret = credentials.issue(name, "shortcut", {"capture:create"}, legacy=payload.get("legacy_token"))
            result = {"token": secret, **credentials.describe(name)}
        elif action == "android":
            path = credentials.setup(name, handler.server.remote_credentials_origin)
            result = {"url": handler.server.remote_credentials_origin + path, "expires_in": 300,
                      "credential": credentials.describe(name)}
        elif action == "revoke":
            result = {"revoked": credentials.revoke(name)}
        elif action == "cancel":
            credentials.cancel_onboarding(name)
            result = {"ok": True}
        else:
            raise ValueError("unknown device action")
    except (ValueError, TypeError) as error:
        raise ProtocolError("invalid_request", str(error)) from None
    handler.answer(200, result)
