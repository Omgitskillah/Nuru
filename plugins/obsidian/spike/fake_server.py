"""WP01 M1 spike: does an authenticated, streamed reply reach a plugin in real Obsidian?

Serves one frozen fixture turn (nuru/tests/fixtures/protocol_v1.json) as protocol v1 server-sent events,
an event every --delay seconds, with the checks nuru-serve will make: loopback only, a bearer token, the
Host and Origin allowed. No model, no Session. Throwaway: nuru-serve (M2) replaces it.

    uv run --locked python plugins/obsidian/spike/fake_server.py [--port 27125] [--delay 0.25]

The token is ~/.nuru/service/spike-token (created 0600 if missing); the spike plugin reads it from there.
"""

import argparse
import hmac
import json
import os
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from core.service import protocol

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = json.loads((ROOT / "nuru/tests/fixtures/protocol_v1.json").read_text())
TOKEN_FILE = Path.home() / ".nuru" / "service" / "spike-token"
ORIGINS = {"app://obsidian.md"}  # a request with no Origin (curl, a native client) is also let through


def token() -> str:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not TOKEN_FILE.exists():
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, secrets.token_urlsafe(32).encode())
        os.close(fd)
    return TOKEN_FILE.read_text().strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "nuru-spike"

    def cors(self):
        origin = self.headers.get("Origin")
        if origin in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def refuse(self, code: str, message: str):
        body = json.dumps(protocol.error_body(code, message)).encode()
        self.send_response(protocol.ERRORS[code])
        self.cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def allowed(self) -> bool:
        if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
            self.refuse("forbidden", "unexpected Host")
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in ORIGINS:
            self.refuse("forbidden_origin", "origin not allowed")
            return False
        return True

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
        if not self.allowed():
            return
        given = self.headers.get("Authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {self.server.token}".encode()):
            return self.refuse("unauthorized", "missing or wrong token")
        url = urlsplit(self.path)
        if url.path == "/v1/status":
            body = json.dumps({"v": 1, "service": "nuru", "version": "spike", "vault": {"id": "vault0000demo", "name": "spike"},
                               "ready": {"chat": "idle", "notes": "ready"}}).encode()
            self.send_response(200)
            self.cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        parts = url.path.split("/")
        if len(parts) == 5 and parts[1:3] == ["v1", "turns"] and parts[4] == "events":
            events = FIXTURES["streams"].get(self.server.stream)
            try:
                cursor = int(parse_qs(url.query).get("after", ["0"])[0])
                todo = protocol.after(events, cursor)
            except ValueError as e:
                return self.refuse(getattr(e, "code", "invalid_request"), str(e))
            self.send_response(200)
            self.cors()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                for event in todo:
                    time.sleep(self.server.delay)
                    self.wfile.write(protocol.sse({**event, "at": protocol.now()}))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                print(f"client left at a cursor below {todo[-1]['seq']}", file=sys.stderr)
            return
        self.refuse("not_found", "no such path")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=protocol.DEFAULT_PORT)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--stream", default="completed_with_tools", choices=sorted(FIXTURES["streams"]))
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.token, server.delay, server.stream = token(), args.delay, args.stream
    print(f"spike on http://127.0.0.1:{args.port}, streaming {args.stream}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
