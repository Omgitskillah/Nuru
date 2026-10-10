"""nuru-serve: Nuru as a local service, speaking protocol v1 (nuru/docs/obsidian-remote/protocol-v1.md)
over HTTP on 127.0.0.1.

    uv run nuru-serve [--port 27125] [--queue 4]
    uv run nuru-serve token add obsidian     # a client's token, for it to read from ~/.nuru/service/clients/
    uv run nuru-serve token remove obsidian
    uv run nuru-serve token list

It starts without loading a model: the first turn loads it. Stopping the service (Ctrl-C) cancels the
turns in hand and leaves Ollama and its models as they were.
"""

import argparse
import json
import os
import re
import signal
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from dotenv import dotenv_values

from core.service import protocol
from core.service.credentials import Credentials
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
]


class Handler(BaseHTTPRequestHandler):
    server_version = "nuru-serve"
    server: "NuruServer"

    def log_message(self, format, *args):  # paths hold IDs only, but there's nothing a user needs here
        pass

    # ------------------------------------------------------------------ answering
    def cors(self) -> None:
        origin = self.headers.get("Origin")
        if origin in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def answer(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def refuse(self, code: str, message: str, **extra) -> None:
        """An error, with the CORS headers of an allowed origin, so the plugin can tell a wrong token
        from a missing service."""
        self.answer(protocol.ERRORS[code], protocol.error_body(code, message, **extra))

    # ------------------------------------------------------------------ who may call
    def allowed(self) -> bool:
        """The Host and Origin checks; a refusal is sent if they fail."""
        port = self.server.server_port
        if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            self.refuse("forbidden", "unexpected Host")
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in ORIGINS:
            self.refuse("forbidden_origin", "origin not allowed")
            return False
        return True

    def principal(self) -> str | None:
        """Whose token this is; None (with 401 sent) if it's missing, wrong or revoked."""
        given = self.headers.get("Authorization", "")
        found = self.server.credentials.check(given[len("Bearer "):]) if given.startswith("Bearer ") else None
        if found is None:
            self.refuse("unauthorized", "missing or wrong token")
            return None
        return found[0]

    def do_OPTIONS(self):  # CORS preflight: the Authorization header makes every plugin request need one
        if not self.allowed():
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

    def route(self, method: str) -> None:
        """All four checks (Host, Origin, token, then ownership in the service) come before anything
        is read from the store or handed to a model."""
        if not self.allowed():
            return
        principal = self.principal()
        if principal is None:
            return
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        try:
            if re.match(r"^/v(?!1/)\d+/", url.path):
                raise ProtocolError("unsupported_version", "this service speaks protocol version 1")
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

    def body(self, operation: str) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > protocol.MAX_REQUEST_BYTES:
            self.close_connection = True  # the body is left unread
            raise ProtocolError("payload_too_large", f"requests are at most {protocol.MAX_REQUEST_BYTES} bytes")
        return protocol.parse_request(operation, self.rfile.read(length))

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
        self.answer(200, self.server.service.status())

    def on_create_conversation(self, principal, query):
        self.answer(*self.server.service.create_conversation(principal, self.body("create_conversation")))

    def on_list_conversations(self, principal, query):
        limit = self.number(query, "limit", 20, 1, 50)
        self.answer(200, self.server.service.list_conversations(principal, self.before(query), limit))

    def on_conversation(self, principal, query, conversation_id):
        limit = self.number(query, "limit", 20, 1, 50)
        self.answer(200, self.server.service.conversation(principal, conversation_id, self.before(query), limit))

    def on_submit_turn(self, principal, query, conversation_id):
        self.answer(*self.server.service.submit_turn(principal, conversation_id, self.body("submit_turn")))

    def on_cancel_turn(self, principal, query, turn_id):
        self.answer(*self.server.service.cancel_turn(principal, turn_id, self.body("cancel_turn")))

    def on_answer_question(self, principal, query, question_id):
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
            events, ended = service.wait_events(turn_id, cursor, self.server.keepalive_seconds)
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


class NuruServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, service: Service, credentials: Credentials, port: int = protocol.DEFAULT_PORT,
                 keepalive_seconds: float = KEEPALIVE_SECONDS) -> None:
        self.service, self.credentials, self.keepalive_seconds = service, credentials, keepalive_seconds
        self.closing = False
        super().__init__(("127.0.0.1", port), Handler)


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


def serve(port: int, queue_limit: int) -> None:
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
    service = Service(store, ConversationStore(session.DB_PATH), _session_factory(vault), vault, queue_limit=queue_limit)
    try:
        server = NuruServer(service, credentials, port)
    except OSError as error:
        service.close()
        store.close()
        service.conversations.close()
        sys.exit(f"nuru-serve: can't listen on 127.0.0.1:{port}: {error.strerror}")

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    print(f"nuru-serve: http://127.0.0.1:{server.server_port}, vault {service.vault.name} ({service.vault_id})", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("nuru-serve: stopping", file=sys.stderr)
    finally:
        server.closing = True
        service.close()
        server.server_close()
        store.close()
        service.conversations.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nuru-serve", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=protocol.DEFAULT_PORT)
    parser.add_argument("--queue", type=int, default=4, help="turns that may wait behind the running one")
    commands = parser.add_subparsers(dest="command")
    token = commands.add_parser("token", help="add, remove or list the clients that may sign in")
    token.add_argument("action", choices=["add", "remove", "list"])
    token.add_argument("client", nargs="?")
    args = parser.parse_args(argv)
    if args.command != "token":
        return serve(args.port, args.queue)
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


if __name__ == "__main__":
    main()
