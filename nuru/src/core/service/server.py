"""nuru-serve: Nuru as a local service, speaking protocol v1 (nuru/docs/obsidian-remote/protocol-v1.md)
over HTTP on 127.0.0.1.

    uv run nuru-serve [--port 27125] [--queue 4] [--managed]
    uv run nuru-serve token add obsidian     # a client's token, for it to read from ~/.nuru/service/clients/
    uv run nuru-serve token remove obsidian
    uv run nuru-serve token list

It starts without loading a model: the first turn loads it. Stopping the service (Ctrl-C) cancels the
turns in hand and leaves Ollama and its models as they were.

--managed is how the Obsidian plugin starts it: it stops when its stdin closes (the plugin that started
it, or Obsidian, has gone), and writes a stop key to ~/.nuru/service/managed.json (owner-only) for
POST /v1/service/stop, with which a plugin in another vault can switch it to its own when it's idle.
A service started any other way has no key, so no plugin can stop it.
"""

import argparse
import hmac
import json
import os
import re
import secrets
import signal
import socket
import sys
import time
import traceback
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from dotenv import dotenv_values

from core.service import protocol
from core.service import remote_contract, remote_access
from core.service.remote_credentials import RemoteCredentials, COOKIE, csrf
from core.service.capture import CaptureError
from core.service.credentials import Credentials, _write_private
from core.service.host import Service
from core.service.protocol import ProtocolError

ORIGINS = {"app://obsidian.md"}  # and no Origin at all: a native client
KEEPALIVE_SECONDS = 15
_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_ROUTES = [  # (method, path pattern, handler name)
    ("GET", r"/v1/status", "status"),
    ("POST", r"/v1/conversations", "create_conversation"),
    ("GET", r"/v1/conversations", "list_conversations"),
    ("GET", r"/v1/conversations/([^/]+)", "conversation"),
    ("POST", r"/v1/conversations/([^/]+)/turns", "submit_turn"),
    ("GET", r"/v1/turns/([^/]+)/events", "events"),
    ("POST", r"/v1/turns/([^/]+)/cancel", "cancel_turn"),
    ("POST", r"/v1/questions/([^/]+)/answers", "answer_question"),
    ("GET", r"/v1/requests/([^/]+)", "request_state"),
    ("POST", r"/v1/service/stop", "stop_service"),
    ("GET", r"/v2/status", "status_v2"),  # protocol v2 is only what changed (protocol-v2.md)
]


class Handler(BaseHTTPRequestHandler):
    server_version = "nuru-serve"
    server: "NuruServer"

    def handle_one_request(self):
        # Socket inactivity timeouts alone do not stop trickled request headers.
        self._header_timer = threading.Timer(5, self._header_timeout)
        self._header_timer.daemon = True
        self._header_timer.start()
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self._header_timer.cancel()

    def _header_timeout(self):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def parse_request(self):
        try:
            return super().parse_request()
        finally:
            self._header_timer.cancel()

    def ensure_active(self):
        if self.server.remote_origin and not self.server.remote_credentials.check_digest(self.device.digest, self.device.kind):
            raise ProtocolError("unauthorized", "credential expired or revoked")

    def log_message(self, format, *args):  # paths hold IDs only, but there's nothing a user needs here
        pass

    # ------------------------------------------------------------------ answering
    def cors(self) -> None:
        origin = self.headers.get("Origin")
        if not self.server.remote_origin and origin in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def answer(self, status: int, body: dict) -> None:
        self.raw_answer(status, json.dumps(body, ensure_ascii=False).encode(), "application/json")

    def raw_answer(self, status: int, data: bytes, media: str) -> None:
        self.send_response(status)
        self.cors()
        self.send_header("Content-Type", media)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in getattr(self, "extra_headers", {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def refuse(self, code: str, message: str, **extra) -> None:
        """An error, with the CORS headers of an allowed origin, so the plugin can tell a wrong token
        from a missing service."""
        self.answer(protocol.ERRORS[code], protocol.error_body(code, message, **extra))

    # ------------------------------------------------------------------ who may call
    def allowed(self, *, capture: bool = False) -> bool:
        """The Host and Origin checks; a refusal is sent if they fail."""
        port = self.server.server_port
        hosts = {urlsplit(self.server.remote_origin).netloc} if self.server.remote_origin else {f"127.0.0.1:{port}", f"localhost:{port}"}
        if len(self.headers.get_all("Host", [])) != 1 or self.headers.get("Host") not in hosts:
            if capture:
                self.capture_answer(403, {"ok": False, "error": "forbidden"})
            else:
                self.refuse("forbidden", "unexpected Host")
            return False
        origin = self.headers.get("Origin")
        origins = {self.server.remote_origin} if self.server.remote_origin else ORIGINS
        if len(self.headers.get_all("Origin", [])) > 1 or (origin is not None and origin not in origins):
            if capture:
                self.capture_answer(403, {"ok": False, "error": "forbidden"})
            else:
                self.refuse("forbidden_origin", "origin not allowed")
            return False
        return True

    def principal(self, *, capture: bool = False) -> tuple[str, str, str] | None:
        """Whose token this is; None (with 401 sent) if it's missing, wrong or revoked."""
        given = self.headers.get("Authorization", "")
        found = self.server.credentials.check_device(given[len("Bearer "):]) if given.startswith("Bearer ") else None
        if found is None:
            if capture:
                self.capture_answer(401, {"ok": False, "error": "unauthorized"})
            else:
                self.refuse("unauthorized", "missing or wrong token")
            return None
        return found

    def do_OPTIONS(self):  # CORS preflight: the Authorization header makes every plugin request need one
        if not self.allowed():
            return
        if self.server.remote_origin and urlsplit(self.path).path.startswith("/v1/android-setup/"):
            if not remote_access.public_route(self, "OPTIONS", urlsplit(self.path).path):
                self.capture_answer(404, {"ok": False, "error": "not_found"})
            return
        self.send_response(204)
        self.cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_HEAD(self):
        self.route("HEAD")

    def do_PUT(self):
        self.route("PUT")

    def do_PATCH(self):
        self.route("PATCH")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method: str) -> None:
        """All four checks (Host, Origin, token, then ownership in the service) come before anything
        is read from the store or handed to a model."""
        url = urlsplit(self.path)
        is_capture = url.path == "/v1/captures" or url.path.startswith("/v1/captures/")
        if not self.allowed(capture=is_capture):
            return
        if len(self.headers.get_all("Authorization", [])) > 1:
            return self.capture_answer(401, {"ok": False, "error": "unauthorized"})
        if self.server.remote_origin and remote_access.public_route(self, method, url.path):
            return
        identity = (remote_access.authenticate(self, method, is_capture) if self.server.remote_origin
                    else self.principal(capture=is_capture))
        if identity is None:
            return
        principal, _, device_id = identity
        query = parse_qs(url.query)
        try:
            if url.path == "/v1/devices" and method == "POST" and not self.server.remote_origin:
                return remote_access.manage(self)
            if self.server.remote_origin and url.path in {"/v1/session", "/v1/logout"}:
                if self.device.kind != "browser":
                    return self.capture_answer(403, {"ok": False, "error": "forbidden"})
                if url.path == "/v1/session" and method == "GET":
                    details = self.server.remote_credentials.describe(self.device.name, digest=self.device.digest)
                    if not details:
                        return self.capture_answer(401, {"ok": False, "error": "unauthorized"})
                    return self.answer(200, {"name": self.device.name, "scopes": sorted(self.device.scopes),
                                             "csrf": csrf(self.session_token),
                                             **details})
                if url.path == "/v1/logout" and method == "POST":
                    self.server.remote_credentials.logout(self.device)
                    self.extra_headers = {"Set-Cookie": f"{COOKIE}=; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"}
                    return self.answer(200, {"ok": True})
                return self.capture_answer(405, {"ok": False, "error": "method_not_allowed"})
            if url.path == "/v1/captures" or url.path.startswith("/v1/captures/"):
                return self.capture_route(method, url.path, device_id)
            if re.match(r"^/v(?!1/)\d+/", url.path) and url.path != "/v2/status":
                raise ProtocolError("unsupported_version", "this service speaks protocol version 1, and version 2's status")
            for route_method, pattern, name in _ROUTES:
                match = re.fullmatch(pattern, url.path)
                if match and route_method == method:
                    ids = match.groups()
                    pattern_for = _REQUEST_ID if name == "request_state" else _ID
                    if not all(pattern_for.match(i) for i in ids):
                        raise ProtocolError("not_found", "no such resource")
                    return getattr(self, "on_" + name)(principal, query, *ids)
            raise ProtocolError("not_found", "no such path")
        except ProtocolError as error:
            self.refuse(error.code, str(error), **error.extra)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client left
        except Exception as error:
            print(f"nuru-serve: {type(error).__name__} answering {method} {url.path}", file=sys.stderr)
            traceback.print_exc(limit=5, file=sys.stderr)
            self.refuse("internal", "Something went wrong in Nuru.")

    def capture_answer(self, status: int, body: dict) -> None:
        self.answer(status, body)

    def capture_route(self, method: str, path: str, device_id: str) -> None:
        try:
            if path == "/v1/captures":
                if method != "POST":
                    raise CaptureError(405, "method_not_allowed")
                if self.server.closing:
                    raise CaptureError(503, "stopping")
                media = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if media != "application/json":
                    raise CaptureError(415, "expected_application_json")
                if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                    raise CaptureError(400, "invalid_capture")
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    raise CaptureError(400, "invalid_capture") from None
                if length < 0:
                    raise CaptureError(400, "invalid_capture")
                if length > remote_contract.MAX_CAPTURE_BYTES:
                    raise CaptureError(413, "body_rejected")
                deadline = time.monotonic() + 5
                chunks, remaining = [], length
                try:
                    while remaining:
                        left = deadline - time.monotonic()
                        if left <= 0:
                            raise CaptureError(408, "body_timeout")
                        self.connection.settimeout(left)
                        # read1 makes at most one socket read; buffered read(n)
                        # can keep accepting trickled bytes past the deadline.
                        part = self.rfile.read1(min(remaining, 64 * 1024))
                        if not part:
                            raise CaptureError(408, "body_timeout")
                        chunks.append(part)
                        remaining -= len(part)
                except (TimeoutError, socket.timeout):
                    raise CaptureError(408, "body_timeout") from None
                self.connection.settimeout(5)
                try:
                    _, payload = remote_contract.parse_request(b"".join(chunks))
                except ValueError:
                    raise CaptureError(400, "invalid_capture") from None
                if self.server.remote_origin and not self.server.remote_credentials.check_digest(self.device.digest, self.device.kind):
                    raise CaptureError(401, "unauthorized")
                return self.capture_answer(*self.server.service.captures.create(device_id, payload))
            match = re.fullmatch(r"/v1/captures/requests/([A-Za-z0-9_-]{16,128})", path)
            if match:
                if method != "GET":
                    raise CaptureError(405, "method_not_allowed")
                return self.capture_answer(*self.server.service.captures.status(device_id, match[1]))
            raise CaptureError(404, "not_found")
        except CaptureError as error:
            self.close_connection = error.code in {"body_timeout", "body_rejected", "invalid_capture"}
            self.capture_answer(error.status, {"ok": False, "error": error.code})

    def read_body(self, limit: int) -> bytes:
        if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
            raise ProtocolError("invalid_request", "one Content-Length is required")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ProtocolError("invalid_request", "invalid Content-Length") from None
        if length < 0:
            raise ProtocolError("invalid_request", "invalid Content-Length")
        if length > limit:
            self.close_connection = True
            raise ProtocolError("payload_too_large", "request is too large")
        deadline, parts = time.monotonic() + 5, []
        while length:
            left = deadline - time.monotonic()
            if left <= 0:
                raise ProtocolError("invalid_request", "upload deadline exceeded")
            self.connection.settimeout(left)
            try:
                part = self.rfile.read1(min(length, 65536))
            except TimeoutError:
                raise ProtocolError("invalid_request", "upload deadline exceeded") from None
            if not part:
                raise ProtocolError("invalid_request", "incomplete body")
            parts.append(part)
            length -= len(part)
        self.connection.settimeout(5)
        return b"".join(parts)

    def json_body(self, limit: int) -> dict:
        try:
            return json.loads(self.read_body(limit))
        except (ValueError, UnicodeError):
            raise ProtocolError("invalid_request", "invalid JSON") from None

    def body(self, operation: str) -> dict:
        body = protocol.parse_request(operation, self.read_body(protocol.MAX_REQUEST_BYTES))
        self.ensure_active()
        return body

    @staticmethod
    def number(query: dict, name: str, default: int, low: int, high: int) -> int:
        try:
            value = int(query.get(name, [default])[0])
        except ValueError:
            raise ProtocolError("invalid_request", f"{name}: should be a whole number") from None
        if not low <= value <= high:
            raise ProtocolError("invalid_request", f"{name}: between {low} and {high}")
        return value

    @staticmethod
    def before(query: dict) -> str | None:
        before = query.get("before", [None])[0]
        if before is not None and not _ID.match(before):
            raise ProtocolError("invalid_request", "before: not in the required format")
        return before

    # ------------------------------------------------------------------ operations
    def on_status(self, principal, query):
        body = self.server.service.status()
        if self.server.remote_origin:
            body.pop("vault", None)
        self.answer(200, body)

    def on_status_v2(self, principal, query):
        body = self.server.service.status_v2()
        if self.server.remote_origin:
            body.pop("vault", None)
        self.answer(200, body)

    def on_stop_service(self, principal, query):
        """Stop a --managed service with its stop key, unless a turn is running or waiting."""
        if self.server.remote_origin or not self.server.stop_key:
            raise ProtocolError("not_found", "no such path")
        key = self.json_body(1024).get("stop_key")
        if not isinstance(key, str) or not hmac.compare_digest(key.encode(), self.server.stop_key.encode()):
            raise ProtocolError("forbidden", "wrong stop key")
        if not self.server.service.close_if_idle():  # from here no turn is accepted
            raise ProtocolError("conflict", "Nuru is busy with a turn in this vault")
        self.answer(202, {"stopping": True})
        print("nuru-serve: stopping, as another vault asked", file=sys.stderr)
        self.server.stop()

    def on_create_conversation(self, principal, query):
        self.answer(*self.server.service.create_conversation(principal, self.body("create_conversation")))

    def on_list_conversations(self, principal, query):
        limit = self.number(query, "limit", 20, 1, 50)
        self.answer(200, self.server.service.list_conversations(principal, self.before(query), limit))

    def on_conversation(self, principal, query, conversation_id):
        limit = self.number(query, "limit", 20, 1, 50)
        self.answer(200, self.server.service.conversation(principal, conversation_id, self.before(query), limit))

    def on_submit_turn(self, principal, query, conversation_id):
        self.server.service.check_target(principal, "conversation", conversation_id)
        options = {}
        if self.server.remote_origin:
            device = self.device
            credentials = self.server.remote_credentials
            options["authorize"] = lambda name: remote_tool_allowed(credentials, device, name)
        self.answer(*self.server.service.submit_turn(principal, conversation_id, self.body("submit_turn"), **options))

    def on_cancel_turn(self, principal, query, turn_id):
        self.server.service.check_target(principal, "turn", turn_id)
        self.answer(*self.server.service.cancel_turn(principal, turn_id, self.body("cancel_turn")))

    def on_answer_question(self, principal, query, question_id):
        self.server.service.check_target(principal, "question", question_id)
        # Protocol questions are live code-issued write approvals. Ordinary model
        # questions end the turn and are answered with the next chat message.
        if self.server.remote_origin and "notes:write" not in self.device.scopes:
            raise ProtocolError("forbidden", "write approval requires notes:write")
        self.answer(*self.server.service.answer_question(principal, question_id, self.body("answer_question")))

    def on_request_state(self, principal, query, request_id):
        operation = query.get("operation", [""])[0]
        if operation not in protocol.OPERATIONS:
            raise ProtocolError("invalid_request", "operation: not an allowed value")
        self.answer(200, self.server.service.request_state(principal, request_id, operation))

    def on_events(self, principal, query, turn_id):
        """Replay the stored events after the cursor, then follow live ones until the terminal event."""
        service = self.server.service
        cursor = self.number(query, "after", 0, 0, 2**31)
        service.check_cursor(principal, turn_id, cursor)
        self.send_response(200)
        self.cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        while True:
            if self.server.remote_origin and not self.server.remote_credentials.check_digest(self.device.digest, self.device.kind):
                return
            try:
                events, ended = service.wait_events(turn_id, cursor, self.server.keepalive_seconds)
            except ProtocolError:
                # The 200 and the stream are under way, so no error body can follow: end the stream,
                # and the client reconnects to find out why.
                self.close_connection = True
                return
            if self.server.remote_origin and not self.server.remote_credentials.check_digest(self.device.digest, self.device.kind):
                return
            for event in events:
                self.wfile.write(protocol.sse(event))
                cursor = event["seq"]
            if not events:
                if ended or self.server.closing:
                    return
                self.wfile.write(protocol.KEEPALIVE)
            self.wfile.flush()
            if events and events[-1]["type"] in protocol.TERMINAL:
                return


def remote_tool_allowed(credentials, device, name):
    current = credentials.check_digest(device.digest, device.kind)
    if current is None or not {"chat:use", "notes:read"} <= current.scopes:
        return False
    if name is None:  # check again before starting a queued model turn
        return True
    if name in {"write_note", "rename_or_move_file"}:
        return {"notes:read", "notes:write"} <= current.scopes
    if name in {"browse", "find_in_notes", "note_info", "read_note", "search", "tags"}:
        return "notes:read" in current.scopes
    # Unknown tools fail closed, including future tools and expert file reads.
    return name in {"calculate", "statistics", "run_python", "tool_gate", "assess_effort",
                    "watch_thinking", "gate_write", "note_type", "rerank", "check_claim"}


class NuruServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_port = False  # two credential boundaries must never share a port
    request_queue_size = 32

    def __init__(self, service: Service, credentials: Credentials, port: int = protocol.DEFAULT_PORT,
                 keepalive_seconds: float = KEEPALIVE_SECONDS, *, remote_origin=None,
                 remote_credentials=None, connections=32) -> None:
        self.service, self.credentials, self.keepalive_seconds = service, credentials, keepalive_seconds
        self.remote_origin = remote_access.https_origin(remote_origin) if remote_origin else None
        if self.remote_origin and remote_credentials is None:
            raise ValueError("remote credentials required")
        self.remote_credentials = remote_credentials
        self.remote_credentials_origin = self.remote_origin
        self.closing = False
        self.stop_key: str | None = None  # set by serve() for a --managed service
        self._slots = threading.BoundedSemaphore(connections)
        super().__init__(("127.0.0.1", port), Handler)

    def stop(self) -> None:
        """End serve_forever from any thread but its own; serve() then shuts down as for Ctrl-C."""
        self.closing = True
        threading.Thread(target=self.shutdown, name="nuru-stop", daemon=True).start()

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(5)  # headers and idle connections are bounded too
        return request, address

    def process_request(self, request, address):
        if self.closing or not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self._slots.release()


# --------------------------------------------------------------------------- the command
def _vault() -> Path:
    from core.session import ENV_DIR  # where Session finds .env.obsidian

    return Path(os.environ.get("VAULT_PATH") or dotenv_values(ENV_DIR / ".env.obsidian").get("VAULT_PATH") or ".")


def _session_factory(vault: Path):
    """Builds the shared Session for nuru-serve: no model until the first turn, and the service's own
    vault for the notes server and the write ledger, so tools read and write the vault that status
    names and attachments are checked against."""
    from core.session import Session

    return lambda conversation_id: Session.start(conversation_id, load_model=False, vault=vault)


def serve(port: int, queue_limit: int, remote_origin: str | None = None, remote_port: int = 27127,
          managed: bool = False) -> None:
    from core import session
    from core.conversation_store import ConversationStore
    from core.service.store import ServiceBusy, ServiceStore

    credentials = Credentials()
    if not credentials.clients():
        print("nuru-serve: no clients can sign in yet; add one with `uv run nuru-serve token add obsidian`", file=sys.stderr)
    try:
        store = ServiceStore(session.DB_PATH.with_name("service.db"))  # owns the database before recovering it
    except ServiceBusy as error:
        sys.exit(f"nuru-serve: {error}")
    vault = _vault().resolve()
    from core.vault_identity import Registry, RegistryError, VaultConflict
    try:
        vault_uuid = Registry().identify(vault)
    except (VaultConflict, RegistryError, ValueError) as error:
        store.close()
        sys.exit(f"nuru-serve: {error}")
    from core import main_models
    default_model = session.MAIN_MODEL if session.CLAUDE else session.BASE_MODEL
    service = Service(store, ConversationStore(session.DB_PATH), _session_factory(vault), vault, queue_limit=queue_limit,
                      models=lambda: main_models.choices(default_model), vault_uuid=vault_uuid)
    if not service.captures._link_available:
        print("nuru-serve: capture unavailable: Inbox cannot publish with safe hard links on this vault",
              file=sys.stderr)
    remote = None
    remote_credentials = None
    try:
        if remote_origin:
            remote_credentials = RemoteCredentials(credentials.directory)
            remote = NuruServer(service, credentials, remote_port, remote_origin=remote_origin,
                                remote_credentials=remote_credentials)
        server = NuruServer(service, credentials, port, remote_credentials=remote_credentials)
        server.remote_credentials_origin = remote_origin
    except OSError as error:
        if remote:
            remote.server_close()
        if remote_credentials:
            remote_credentials.close()
        service.close()
        store.close()
        service.conversations.close()
        sys.exit(f"nuru-serve: can't listen on 127.0.0.1:{port}: {error.strerror}")

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    managed_file = credentials.directory / "managed.json"
    if managed:
        _manage(server, managed_file, sys.stdin.buffer)
    print(f"nuru-serve: http://127.0.0.1:{server.server_port}, vault {service.vault.name} ({service.vault_id})", file=sys.stderr)
    if remote:
        threading.Thread(target=remote.serve_forever, name="nuru-remote", daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("nuru-serve: stopping", file=sys.stderr)
    finally:
        server.closing = True
        if managed:
            _forget_managed(managed_file)
        if remote:
            remote.closing = True
            remote.shutdown()
        stopped = service.close()
        server.server_close()
        if remote:
            remote.server_close()
        # Daemon HTTP threads can still be unwinding; process exit closes credentials.
        if stopped:
            store.close()
            service.conversations.close()
        else:
            # The worker may still be using the database: keep it owned until this process ends.
            print("nuru-serve: a turn didn't stop in time; exiting with the database still owned", file=sys.stderr)


def _manage(server: "NuruServer", path: Path, stdin) -> None:
    """--managed: a stop key in `path` (owner-only), and a stop when `stdin` closes. It's a pipe from the
    plugin that started the service, closed when that plugin or Obsidian is gone, even if it crashed."""
    server.stop_key = secrets.token_urlsafe(32)
    _write_private(path, json.dumps({"pid": os.getpid(), "port": server.server_port, "stop_key": server.stop_key}))

    def orphaned():
        try:
            stdin.read()
        except (OSError, ValueError):
            pass
        print("nuru-serve: the Obsidian plugin that started it is gone; stopping", file=sys.stderr)
        server.stop()

    threading.Thread(target=orphaned, name="nuru-managed", daemon=True).start()


def _forget_managed(path: Path) -> None:
    """Remove managed.json if it's still this process's: a newer service may have replaced it."""
    try:
        if json.loads(path.read_text()).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nuru-serve", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=protocol.DEFAULT_PORT)
    parser.add_argument("--queue", type=int, default=4, help="turns that may wait behind the running one")
    parser.add_argument("--remote-origin", help="opt in: private HTTPS Tailscale origin")
    parser.add_argument("--remote-port", type=int, default=27127)
    parser.add_argument("--managed", action="store_true",
                        help="started by the Obsidian plugin: stop when stdin closes; another vault may stop it when idle")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("remote-check", help="inspect Tailscale HTTPS target and reject public Funnel; read-only")
    vault_command = commands.add_parser("vault", help="vault IDs on this computer: list, or settle a copied vault")
    vault_command.add_argument("action", choices=["list", "rebind", "clone"])
    vault_command.add_argument("vault", nargs="?", help="the vault to rebind (keep its ID) or clone (give it a new one)")
    vault_command.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    token = commands.add_parser("token", help="add, remove or list the clients that may sign in")
    token.add_argument("action", choices=["add", "remove", "list"])
    token.add_argument("client", nargs="?")
    device = commands.add_parser("device", help="host-only device approval/rotation; service must be running")
    device.add_argument("action", choices=["pair", "shortcut", "android", "revoke", "cancel", "list"])
    device.add_argument("name", nargs="?")
    device.add_argument("--scopes", nargs="+", default=[])
    device.add_argument("--local-client", default="obsidian")
    device.add_argument("--import-legacy", action="store_true", help="prompt privately for an existing capture token")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or not 1 <= args.remote_port <= 65535:
        parser.error("listener ports must be between 1 and 65535")
    if args.remote_origin and (args.remote_port in {27125, 27126} or args.remote_port == args.port):
        parser.error("remote listener needs a separate port; 27125 and 27126 are reserved")
    if args.remote_origin:
        try:
            args.remote_origin = remote_access.https_origin(args.remote_origin)
        except ValueError as error:
            parser.error(str(error))
    if args.command == "remote-check":
        from core.service.private_transport import inspect
        if not args.remote_origin:
            parser.error("remote-check requires --remote-origin")
        try:
            problems = inspect(args.remote_origin, args.remote_port)
        except ValueError as error:
            parser.error(str(error))
        if problems:
            parser.error("; ".join(problems))
        print("Private HTTPS route targets the remote listener; Funnel is disabled.")
        return
    if args.command == "device":
        return device_command(args, parser)
    if args.command == "vault":
        return vault_command_run(args, parser)
    if args.command != "token":
        return serve(args.port, args.queue, args.remote_origin, args.remote_port, args.managed)
    credentials = Credentials()
    if args.action == "list":
        print("\n".join(credentials.clients()) or "no clients")
    elif not args.client:
        parser.error(f"token {args.action} needs a client name")
    elif args.action == "add":
        try:
            credentials.add(args.client)
        except ValueError as error:
            parser.error(str(error))
        print(f"{args.client}: token written to {credentials.directory / 'clients' / (args.client + '.token')}")
    elif not credentials.remove(args.client):
        parser.error(f"no client named {args.client}")
    else:
        print(f"{args.client}: token revoked")


def vault_command_run(args, parser):
    """`vault list`, and `vault rebind|clone <vault>` for a vault refused as a copy (core.vault_identity)."""
    from core.vault_identity import Registry, RegistryError, read
    registry = Registry()
    if args.action == "list":
        try:
            entries = registry.entries()
        except RegistryError as error:
            sys.exit(f"nuru-serve: {error}")
        for vault_uuid, path in sorted(entries.items(), key=lambda item: item[1]):
            here = "" if read(Path(path)) == vault_uuid else "  (no longer there)"
            print(f"{vault_uuid}  {path}{here}")
        return
    if not args.vault:
        parser.error(f"vault {args.action} needs the vault's folder")
    vault = Path(args.vault).expanduser().resolve()
    if not vault.is_dir():
        parser.error(f"no folder at {vault}")
    what = (f"Keep {vault} as the vault with this ID and its history; the other copy is treated as old."
            if args.action == "rebind" else f"Give {vault} a new ID: a separate vault, starting with no history.")
    if not args.yes and input(f"{what} Go ahead? [y/N] ").strip().lower() not in {"y", "yes"}:
        print("Nothing changed.")
        return
    try:
        vault_uuid = registry.rebind(vault) if args.action == "rebind" else registry.clone(vault)
    except (ValueError, RegistryError) as error:
        parser.error(str(error))
    print(f"{vault}: vault ID {vault_uuid}")


def device_command(args, parser):
    import getpass
    import http.client
    from core.service.credentials import _CLIENT_NAME
    if not _CLIENT_NAME.fullmatch(args.local_client):
        parser.error("invalid local client name")
    payload = {"action": args.action, "name": args.name, "scopes": args.scopes}
    if args.import_legacy:
        if args.action != "shortcut":
            parser.error("--import-legacy requires shortcut")
        payload["legacy_token"] = getpass.getpass("Existing capture token (hidden): ")
    try:
        token = (Credentials().directory / "clients" / (args.local_client + ".token")).read_text().strip()
        connection = http.client.HTTPConnection("127.0.0.1", args.port, timeout=10)
        connection.request("POST", "/v1/devices", json.dumps(payload),
                           {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
    except (OSError, ValueError) as error:
        parser.error(f"cannot contact the local service: {type(error).__name__}")
    if response.status != 200:
        parser.error("device operation failed: " + json.dumps(data))
    print(json.dumps(data, indent=2))  # explicit operator output only; never service logs


if __name__ == "__main__":
    main()
