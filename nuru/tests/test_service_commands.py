"""Host CLI and whole-service lifetimes, with scratch state and real local HTTP/SQLite.

No model, external command, user's registry, vault or credential file is touched.
"""
import errno
import http.client
import json
import os
import signal
import socket
import sqlite3
import threading
from types import SimpleNamespace
from contextlib import contextmanager
from pathlib import Path

import pytest

from core import main_models, session, vault_data, vault_identity
from core.service import server
from core.service.credentials import Credentials
from core.service.protocol import ProtocolError
from core.service.store import ServiceStore
from test_service_server import Client


@pytest.fixture(autouse=True)
def host_state(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("VAULT_PATH", str(vault))
    monkeypatch.setenv("NURU_SERVICE_DIR", str(tmp_path / "service"))
    monkeypatch.setenv("NURU_VAULTS_DIR", str(tmp_path / "vaults"))
    monkeypatch.setattr(vault_data, "LEGACY_DB", tmp_path / "old-db")
    monkeypatch.setattr(vault_data, "LEGACY_LOGS", tmp_path / "old-logs")
    monkeypatch.setattr(session, "DB_PATH", None)
    monkeypatch.setattr(session, "LOG_DIR", None)
    monkeypatch.setattr(main_models, "choices", lambda default: {"default": default, "choices": []})
    return vault


@pytest.mark.parametrize("args, message", [
    (["--port", "0"], "between 1 and 65535"),
    (["--remote-port", "65536"], "between 1 and 65535"),
    (["--remote-origin", "https://nuru.test.ts.net", "--remote-port", "27125"], "separate port"),
    (["--remote-origin", "https://nuru.test.ts.net", "--remote-port", "27126"], "separate port"),
    (["--port", "30000", "--remote-port", "30000", "--remote-origin", "https://nuru.test.ts.net"], "separate port"),
    (["--remote-origin", "http://public.example"], "HTTPS"),
    (["remote-check"], "requires --remote-origin"),
    (["token", "add"], "needs a client name"),
    (["token", "remove", "missing"], "no client named"),
    (["vault", "clone"], "needs the vault's folder"),
    (["vault", "clone", "/a-folder-that-does-not-exist"], "no folder"),
    (["device", "list", "--local-client", "../escape"], "invalid local client"),
    (["device", "pair", "phone", "--import-legacy"], "requires shortcut"),
    (["device", "list"], "cannot contact the local service: FileNotFoundError"),
])
def test_invalid_host_commands_do_not_start_a_service(args, message, monkeypatch, capsys):
    monkeypatch.setattr(server, "serve", lambda *a: pytest.fail("invalid command started a service"))
    with pytest.raises(SystemExit) as stopped:
        server.main(args)
    assert stopped.value.code == 2
    assert message.lower() in capsys.readouterr().err.lower()
    assert not list(Path(os.environ["NURU_VAULTS_DIR"]).glob("*/db/*"))


def test_cli_forwards_valid_listener_options_without_starting_a_model(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "serve", lambda *args: calls.append(args))
    server.main(["--port", "30100", "--remote-origin", "https://nuru.test.ts.net", "--remote-port", "30101", "--queue", "2", "--managed"])
    assert calls == [(30100, 2, "https://nuru.test.ts.net", 30101, True)]


@pytest.mark.parametrize("result, message", [([], "Funnel is disabled"), (["Funnel is public"], "Funnel is public"),
                                           (ValueError("invalid proxy target"), "invalid proxy target")])
def test_remote_check_reports_transport_diagnostics(result, message, monkeypatch, capsys):
    from core.service import private_transport
    seen = []
    def inspect(origin, port):
        seen.append((origin, port))
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(private_transport, "inspect", inspect)
    args = ["--remote-origin", "https://nuru.test.ts.net", "--remote-port", "30101", "remote-check"]
    if result:
        with pytest.raises(SystemExit):
            server.main(args)
    else:
        server.main(args)
    output = capsys.readouterr()
    assert message in output.out + output.err
    assert seen == [("https://nuru.test.ts.net", 30101)]


def test_empty_token_registry_and_invalid_client_report_no_secrets(capsys):
    server.main(["token", "list"])
    assert capsys.readouterr().out.strip() == "no clients"
    with pytest.raises(SystemExit):
        server.main(["token", "add", "../escape"])
    assert not Credentials().clients()


def test_vault_commands_list_clone_and_rebind_the_real_registry(host_state, capsys):
    server.main(["vault", "clone", str(host_state), "--yes"])
    original = vault_identity.read(host_state)
    assert original
    server.main(["vault", "list"])
    assert f"{original}  {host_state}" in capsys.readouterr().out
    server.main(["vault", "rebind", str(host_state), "--yes"])
    assert vault_identity.read(host_state) == original
    (host_state / vault_identity.ID_FILE).unlink()
    server.main(["vault", "list"])
    assert "no longer there" in capsys.readouterr().out


def test_declining_vault_change_preserves_identity(host_state, monkeypatch, capsys):
    old = vault_identity.Registry().identify(host_state)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    server.main(["vault", "clone", str(host_state)])
    assert vault_identity.read(host_state) == old
    assert "Nothing changed" in capsys.readouterr().out


@pytest.mark.parametrize("action", ["list", "clone", "rebind"])
def test_unreadable_registry_or_conflict_produces_an_actionable_cli_error(host_state, monkeypatch, capsys, action):
    def broken(*args):
        raise vault_identity.RegistryError("registry cannot be read")
    monkeypatch.setattr(vault_identity.Registry, {"list": "entries", "clone": "clone", "rebind": "rebind"}[action], broken)
    args = ["vault", action] + ([] if action == "list" else [str(host_state), "--yes"])
    with pytest.raises(SystemExit) as stopped:
        server.main(args)
    assert "registry cannot be read" in str(stopped.value) + capsys.readouterr().err


@pytest.mark.parametrize("fail", [False, True])
def test_adopt_legacy_command_reports_counts_or_preserves_failure(host_state, monkeypatch, capsys, fail):
    calls = []
    def adopt(vault_uuid):
        calls.append(vault_uuid)
        if fail:
            raise vault_data.MigrationError("source is still owned")
        return {"conversations": 2, "logs": 3, "kept": ["conversation.db.pre-wp04"]}
    monkeypatch.setattr(vault_data, "adopt_legacy", adopt)
    args = ["vault", "adopt-legacy", str(host_state), "--yes"]
    if fail:
        with pytest.raises(SystemExit, match="nothing moved: source is still owned"):
            server.main(args)
    else:
        server.main(args)
        text = capsys.readouterr().out
        assert "2 conversations and 3 log files" in text and "conversation.db.pre-wp04" in text
    assert calls == [vault_identity.read(host_state)]


@pytest.mark.parametrize("status, raw, error", [(200, b'{"paired": true}', None), (403, b'{"error": "denied"}', "device operation failed"),
                                                (200, b'not JSON', "JSONDecodeError")])
def test_device_command_uses_local_token_and_private_import(status, raw, error, monkeypatch, capsys):
    import getpass
    token = Credentials().add("obsidian")
    calls = []
    class Connection:
        def __init__(self, host, port, timeout):
            calls.append((host, port, timeout))
        def request(self, method, path, data, headers):
            calls.append((method, path, json.loads(data), headers))
        def getresponse(self):
            return SimpleNamespace(status=status, read=lambda: raw)
        def close(self):
            calls.append("closed")
    monkeypatch.setattr(http.client, "HTTPConnection", Connection)
    monkeypatch.setattr(getpass, "getpass", lambda prompt: "private-import-token")
    args = ["--port", "30123", "device", "shortcut", "phone", "--import-legacy", "--scopes", "notes:read"]
    if error:
        with pytest.raises(SystemExit):
            server.main(args)
        assert error in capsys.readouterr().err
    else:
        server.main(args)
        assert json.loads(capsys.readouterr().out) == {"paired": True}
        assert calls[-1] == "closed"
    assert calls[0] == ("127.0.0.1", 30123, 10)
    method, path, payload, headers = calls[1]
    assert (method, path) == ("POST", "/v1/devices")
    assert payload == {"action": "shortcut", "name": "phone", "scopes": ["notes:read"], "legacy_token": "private-import-token"}
    assert headers["Authorization"] == "Bearer " + token


@pytest.mark.parametrize("remote, stopped", [(False, True), (True, True), (False, False)])
def test_serve_managed_stdin_shutdown_releases_listeners_and_database_ownership(host_state, tmp_path, monkeypatch, capsys, remote, stopped):
    """Run serve itself: real HTTP, real stores and stdin ownership, no Session/model call."""
    token = Credentials().add("obsidian")
    servers, services, failures, answers = [], [], [], []
    ready = threading.Event()
    real_server, real_service = server.NuruServer, server.Service
    def make_service(*args, **kwargs):
        service = real_service(*args, **kwargs)
        services.append(service)
        if not stopped:
            close = service.close
            service.close = lambda *a, **kw: (close(*a, **kw), False)[1]
        return service
    def make_server(*args, **kwargs):
        made = real_server(*args, **kwargs)
        servers.append(made)
        if not kwargs.get("remote_origin"):
            ready.set()
        return made
    registered, built = [], []
    monkeypatch.setattr(server, "Service", make_service)
    monkeypatch.setattr(server, "NuruServer", make_server)
    monkeypatch.setattr(server.signal, "signal", lambda sig, handler: registered.append((sig, handler)))
    monkeypatch.setattr(server, "_session_factory", lambda vault: lambda cid: built.append((vault, cid)))
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as stdin, os.fdopen(write_fd, "wb") as owner:
        monkeypatch.setattr(server.sys, "stdin", SimpleNamespace(buffer=stdin))
        def client():
            try:
                assert ready.wait(5), "listener was not constructed"
                port = servers[-1].server_port
                answers.append(Client(port, token).call("GET", "/v2/status")[:2])
            except BaseException as exc:
                failures.append(exc)
            finally:
                owner.close()  # the plugin goes away: serve must return, not just its HTTP loop
        peer = threading.Thread(target=client, daemon=True)
        timed_out = threading.Event()
        def deadline():
            timed_out.set()
            for instance in servers:
                instance.stop()
        watchdog = threading.Timer(30, deadline)
        watchdog.daemon = True
        watchdog.start()
        peer.start()
        try:
            server.serve(0, 2, "https://nuru.test.ts.net" if remote else None, 0, managed=True)
        finally:
            watchdog.cancel()
            owner.close()
            for instance in servers:
                if instance.socket.fileno() != -1:
                    instance.server_close()
            peer.join(10)
    assert not timed_out.is_set(), "managed EOF failed to stop serve within its test deadline"
    assert not peer.is_alive() and not failures
    assert built == [], "status must not create a Session on the service worker"
    assert answers[0][0] == 200 and answers[0][1]["vault"]["name"] == "vault"
    assert not (Credentials().directory / "managed.json").exists()
    assert all(instance.closing and instance.socket.fileno() == -1 for instance in servers)
    for instance in servers:
        if instance.remote_credentials is not None:
            instance.remote_credentials.close()
    assert registered[0][0] == signal.SIGTERM
    with pytest.raises(KeyboardInterrupt):
        registered[0][1](signal.SIGTERM, None)
    data = session.data_paths(host_state)[0]
    if stopped:
        with pytest.raises(sqlite3.ProgrammingError):
            services[0].store.title("missing")
    else:
        assert services[0].store.title("missing") is None, "a still-running worker must retain database ownership"
        services[0].store.close()
        services[0].conversations.close()
        assert "database still owned" in capsys.readouterr().err
    with _reopened(data.with_name("service.db")):
        pass


@contextmanager
def _reopened(path):
    store = ServiceStore(path)
    try:
        yield store
    finally:
        store.close()


def test_serve_reports_registry_failure_before_creating_any_listener(host_state, monkeypatch):
    def conflict(*args):
        raise vault_identity.RegistryError("vault identity is unavailable")
    monkeypatch.setattr(vault_identity.Registry, "identify", conflict)
    monkeypatch.setattr(server, "NuruServer", lambda *a, **k: pytest.fail("listener created"))
    with pytest.raises(SystemExit, match="vault identity is unavailable"):
        server.serve(0, 1)


def test_bind_failure_closes_prepared_stores_and_remote_listener(host_state, monkeypatch):
    made, services = [], []
    real_server, real_service = server.NuruServer, server.Service
    def make_service(*args, **kwargs):
        item = real_service(*args, **kwargs)
        services.append(item)
        return item
    def bind(*args, **kwargs):
        if not kwargs.get("remote_origin"):
            raise OSError(errno.EADDRINUSE, "address in use")
        item = real_server(*args, **kwargs)
        made.append(item)
        return item
    monkeypatch.setattr(server, "Service", make_service)
    monkeypatch.setattr(server, "NuruServer", bind)
    with pytest.raises(SystemExit, match="can't listen.*address in use"):
        server.serve(0, 1, "https://nuru.test.ts.net", 0)
    assert made[0].socket.fileno() == -1
    assert not services[0]._worker.is_alive()
    with _reopened(session.data_paths(host_state)[0].with_name("service.db")):
        pass


@pytest.mark.parametrize("content", ["not json", '{"pid": 1}'])
def test_forget_managed_does_not_delete_an_unknown_owner(tmp_path, content):
    path = tmp_path / "managed.json"
    path.write_text(content, encoding="utf-8")
    server._forget_managed(path)
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("mode, problem", [("trickle", "deadline"), ("timeout", "deadline"), ("truncated", "incomplete body")])
def test_request_body_has_a_total_deadline_and_rejects_truncation(monkeypatch, mode, problem):
    """A progressing upload cannot reset the total budget; no sleeps or real stalled sockets."""
    from email.message import Message
    handler = server.Handler.__new__(server.Handler)
    handler.headers = Message()
    handler.headers["Content-Length"] = "2"
    clocks = iter([10.0, 11.0, 16.0])
    reads, timeouts = [], []
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: next(clocks, 16.0)))
    def read1(size):
        reads.append(size)
        if mode == "timeout":
            raise TimeoutError
        return b"a" if mode == "trickle" else b""
    handler.rfile = SimpleNamespace(read1=read1)
    handler.connection = SimpleNamespace(settimeout=timeouts.append)
    with pytest.raises(ProtocolError, match=problem):
        handler.read_body(10)
    assert reads == [2], "neither a new byte nor another read extends the budget"
    assert timeouts == [4.0]


@pytest.mark.parametrize("length", ["-1", "not-a-number"])
def test_invalid_body_lengths_fail_before_reading(length):
    from email.message import Message
    handler = server.Handler.__new__(server.Handler)
    handler.headers = Message()
    handler.headers["Content-Length"] = length
    handler.rfile = SimpleNamespace(read1=lambda n: pytest.fail("read an invalid body length"))
    with pytest.raises(ProtocolError, match="invalid Content-Length"):
        handler.read_body(10)


def test_managed_stdin_read_failure_still_stops_the_service(tmp_path, capsys):
    stopped = threading.Event()
    class Gone:
        def read(self):
            raise OSError("owner's pipe broke")
    instance = SimpleNamespace(server_port=30000, stop=stopped.set)
    path = tmp_path / "managed.json"
    server._manage(instance, path, Gone())
    assert stopped.wait(5)
    assert "plugin that started it is gone" in capsys.readouterr().err
    assert json.loads(path.read_text(encoding="utf-8"))["stop_key"] == instance.stop_key
    server._forget_managed(path)
    assert not path.exists()


@pytest.mark.parametrize("payload", [b"not json", b'"\xff"'])
def test_admin_json_rejects_malformed_and_non_utf8_without_exposing_payload(payload):
    handler = server.Handler.__new__(server.Handler)
    handler.read_body = lambda limit: payload
    with pytest.raises(ProtocolError, match="invalid JSON") as refused:
        handler.json_body(100)
    assert str(refused.value) == "invalid JSON"


@pytest.mark.parametrize("query", [{"limit": ["many"]}, {"limit": ["0"]}, {"limit": ["101"]}])
def test_paging_bounds_reject_invalid_numbers(query):
    with pytest.raises(ProtocolError):
        server.Handler.number(query, "limit", 50, 1, 100)


def test_invalid_history_cursor_is_refused_before_access():
    with pytest.raises(ProtocolError, match="before: not in the required format"):
        server.Handler.before({"before": ["../other-history"]})


def test_revocation_is_rechecked_before_dispatching_a_body():
    handler = server.Handler.__new__(server.Handler)
    checked = []
    handler.device = SimpleNamespace(digest="revoked-digest", kind="browser")
    handler.server = SimpleNamespace(remote_origin="https://nuru.test.ts.net", remote_credentials=SimpleNamespace(
        check_digest=lambda digest, kind: checked.append((digest, kind)) or False))
    with pytest.raises(ProtocolError, match="expired or revoked"):
        handler.ensure_active()
    assert checked == [("revoked-digest", "browser")]


def test_a_header_deadline_after_socket_close_is_harmless():
    handler = server.Handler.__new__(server.Handler)
    shutdown = []
    def closed(how):
        shutdown.append(how)
        raise OSError("already closed")
    handler.connection = SimpleNamespace(shutdown=closed)
    handler._header_timeout()
    assert shutdown == [socket.SHUT_RDWR]


def test_failed_http_worker_dispatch_returns_the_connection_slot(monkeypatch):
    """A thread-start failure cannot slowly exhaust all connection admission slots."""
    instance = server.NuruServer.__new__(server.NuruServer)
    instance.closing = False
    instance._slots = threading.BoundedSemaphore(1)
    def exhausted(*args):
        raise RuntimeError("cannot start HTTP worker")
    monkeypatch.setattr(server.ThreadingHTTPServer, "process_request", exhausted)
    with pytest.raises(RuntimeError, match="cannot start HTTP worker"):
        instance.process_request(object(), ("127.0.0.1", 1234))
    assert instance._slots.acquire(blocking=False), "dispatch failure leaked the only slot"
    instance._slots.release()


def test_remote_listener_without_credential_registry_never_binds():
    with pytest.raises(ValueError, match="remote credentials required"):
        server.NuruServer(None, None, port=0, remote_origin="https://nuru.test.ts.net")


@pytest.mark.parametrize("closing, available", [(True, 1), (False, 0)])
def test_closing_or_full_listener_refuses_without_creating_a_worker(closing, available, monkeypatch):
    instance = server.NuruServer.__new__(server.NuruServer)
    instance.closing = closing
    instance._slots = threading.BoundedSemaphore(available)
    rejected = []
    instance.shutdown_request = rejected.append
    monkeypatch.setattr(server.ThreadingHTTPServer, "process_request", lambda *a: pytest.fail("admitted a refused request"))
    connection = object()
    instance.process_request(connection, ("127.0.0.1", 1234))
    assert rejected == [connection]
    if available:
        assert instance._slots.acquire(blocking=False), "refusal consumed an admission slot"
        instance._slots.release()
