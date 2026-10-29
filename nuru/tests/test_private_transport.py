"""F13: the read-only Tailscale checks (core.service.private_transport). Tailscale itself isn't run: its CLI is
faked at the subprocess boundary with the JSON shapes `tailscale status --json` and `serve status --json` give."""
import json
import subprocess

import pytest

from core.service import private_transport
from core.service.private_transport import check_state, inspect

ORIGIN = "https://host.tail.ts.net"


def status(**changes):
    return {"BackendState": "Running", "Self": {"DNSName": "host.tail.ts.net."}, **changes}


def serve(port=27127, **changes):
    return {"TCP": {"443": {"HTTPS": True}},
            "Web": {"host.tail.ts.net:443": {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{port}"}}}}, **changes}


def test_a_single_private_https_route_to_the_remote_listener_passes():
    assert check_state(status(), serve(), ORIGIN) == []
    assert check_state(status(), serve(port=27200), ORIGIN, port=27200) == []


@pytest.mark.parametrize("state, problem", [
    (status(BackendState="Stopped"), "Tailscale must be running on the configured HTTPS hostname"),
    (status(Self={"DNSName": "other.tail.ts.net."}), "Tailscale must be running on the configured HTTPS hostname"),
    (status(Self={}), "Tailscale must be running on the configured HTTPS hostname"),
])
def test_tailscale_not_running_on_the_configured_host_is_a_problem(state, problem):
    assert check_state(state, serve(), ORIGIN) == [problem]


@pytest.mark.parametrize("routes, problem", [
    (serve(TCP={"443": {"TCPForward": "127.0.0.1:27127"}}), "Serve port 443 must terminate HTTPS"),
    (serve(port=27125), "Serve / must target only the configured remote listener (default 127.0.0.1:27127)"),
    (serve(Web={}), "Serve / must target only the configured remote listener (default 127.0.0.1:27127)"),
    (serve(AllowFunnel={"host.tail.ts.net:443": True}), "Funnel must be disabled"),
    (serve(Foreground={"session": {"Web": {}}}), "remove foreground Serve overrides before verifying the route"),
])
def test_each_unsafe_serve_setting_is_named(routes, problem):
    assert check_state(status(), routes, ORIGIN) == [problem]


def test_the_local_listener_port_is_refused_even_if_serve_points_at_it():
    """27125 is the local, operator-only listener: it must never be published, whatever Serve says."""
    assert check_state(status(), serve(port=27125), ORIGIN, port=27125) == [
        "Serve / must target only the configured remote listener (default 127.0.0.1:27127)"]


def test_funnel_is_found_wherever_it_is_nested_and_a_disabled_one_is_fine():
    nested = serve(Background={"AllowFunnel": {"host.tail.ts.net:443": True}})
    assert check_state(status(), nested, ORIGIN) == ["Funnel must be disabled"]
    off = serve(AllowFunnel={"host.tail.ts.net:443": False})
    assert check_state(status(), off, ORIGIN) == []


def test_an_origin_that_is_not_a_tailnet_https_host_is_refused():
    with pytest.raises(ValueError, match="ts.net"):
        check_state(status(), serve(), "http://host.tail.ts.net")


@pytest.fixture
def tailscale(monkeypatch):
    """`tailscale` answering `status --json` and `serve status --json` from `answers`; records each command."""
    calls, answers = [], {"status": json.dumps(status()), "serve": json.dumps(serve())}

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 10 and kwargs["check"] is True
        answer = answers["serve" if command[1] == "serve" else "status"]
        if isinstance(answer, BaseException):
            raise answer
        return subprocess.CompletedProcess(command, 0, answer, "")

    monkeypatch.setattr(private_transport.subprocess, "run", run)
    return calls, answers


def test_inspect_reads_both_states_from_the_cli_and_changes_nothing(tailscale):
    calls, _ = tailscale
    assert inspect(ORIGIN, executable="/usr/bin/tailscale") == []
    assert calls == [["/usr/bin/tailscale", "status", "--json"], ["/usr/bin/tailscale", "serve", "status", "--json"]]


@pytest.mark.parametrize("failure", [subprocess.CalledProcessError(1, "tailscale"), subprocess.TimeoutExpired("tailscale", 10),
                                     OSError("permission denied"), "not json"])
def test_a_cli_that_fails_or_answers_garbage_is_one_clear_error(tailscale, failure):
    _, answers = tailscale
    answers["serve"] = failure
    with pytest.raises(ValueError, match="cannot read Tailscale state; check Tailscale on this host"):
        inspect(ORIGIN, executable="tailscale")


def test_inspect_finds_the_cli_on_path_then_in_the_mac_app_and_says_when_neither_exists(tailscale, monkeypatch):
    calls, _ = tailscale
    monkeypatch.setattr(private_transport.shutil, "which", lambda name: "/opt/bin/tailscale")
    inspect(ORIGIN)
    assert calls[-1][0] == "/opt/bin/tailscale"

    monkeypatch.setattr(private_transport.shutil, "which", lambda name: None)
    monkeypatch.setattr(private_transport.Path, "exists", lambda self: True)
    inspect(ORIGIN)
    assert calls[-1][0] == str(private_transport.Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"))  # native separators

    monkeypatch.setattr(private_transport.Path, "exists", lambda self: False)
    with pytest.raises(ValueError, match="Tailscale CLI is unavailable"):
        inspect(ORIGIN)
