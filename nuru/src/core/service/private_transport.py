"""Read-only checks of Tailscale's live JSON state. Never changes Serve/Funnel."""
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from core.service.remote_access import https_origin


def check_state(status: dict, serve: dict, origin: str, port: int = 27127) -> list[str]:
    origin = https_origin(origin)
    problems = []
    host = urlsplit(origin).hostname
    if status.get("BackendState") != "Running" or status.get("Self", {}).get("DNSName", "").rstrip(".") != host:
        problems.append("Tailscale must be running on the configured HTTPS hostname")
    if serve.get("TCP", {}).get("443", {}).get("HTTPS") is not True:
        problems.append("Serve port 443 must terminate HTTPS")
    route = serve.get("Web", {}).get(host + ":443", {}).get("Handlers", {}).get("/", {})
    if port == 27125 or route != {"Proxy": f"http://127.0.0.1:{port}"}:
        problems.append("Serve / must target only the configured remote listener (default 127.0.0.1:27127)")

    def funnel_enabled(value):
        if not isinstance(value, dict):
            return False
        return any((key == "AllowFunnel" and isinstance(child, dict) and any(child.values()))
                   or funnel_enabled(child) for key, child in value.items())
    if funnel_enabled(serve):
        problems.append("Funnel must be disabled")
    # Foreground Serve sessions may contain alternate effective routing. Fail closed
    # until the operator removes them and a single persistent route can be verified.
    if serve.get("Foreground"):
        problems.append("remove foreground Serve overrides before verifying the route")
    return problems


def inspect(origin: str, port: int = 27127, executable: str | None = None) -> list[str]:
    executable = executable or shutil.which("tailscale")
    if executable is None:
        app = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
        executable = str(app) if app.exists() else None
    if executable is None:
        raise ValueError("Tailscale CLI is unavailable")
    results = []
    for args in (["status", "--json"], ["serve", "status", "--json"]):
        try:
            result = subprocess.run([executable, *args], capture_output=True, text=True, timeout=10, check=True)
            results.append(json.loads(result.stdout))
        except (OSError, subprocess.SubprocessError, ValueError):
            raise ValueError("cannot read Tailscale state; check Tailscale on this host") from None
    return check_state(*results, origin, port)
