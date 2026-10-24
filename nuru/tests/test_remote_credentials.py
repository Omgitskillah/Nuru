"""No providers: lifecycle, durable revocation, bounded onboarding and scope checks."""
import concurrent.futures
import stat

import pytest

from core.service.remote_credentials import DAY, PAIR_ALPHABET, PAIR_FAILURE_LIMIT, PairingLimited, RemoteCredentials
from core.service.server import remote_tool_allowed
from core.service.private_transport import check_state


@pytest.fixture
def credentials(tmp_path):
    now = [1000.0]
    store = RemoteCredentials(tmp_path, clock=lambda: now[0])
    yield store, now
    store.close()


def test_pairing_is_single_use_even_under_race(credentials):
    store, _ = credentials
    code = store.issue("phone", "browser", {"chat:use"})
    assert store.authenticate(code, "browser") is None
    def redeem(code):
        try:
            return store.redeem(code)
        except PairingLimited:
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(redeem, [code] * 32))
    tokens = [t for t in results if t]
    assert len(tokens) == 1
    device = store.authenticate(tokens[0], "browser")
    assert device.scopes == {"chat:use"}
    assert store.authenticate(tokens[0], "shortcut") is None


def test_expiry_idle_absolute_and_logout(credentials):
    store, now = credentials
    code = store.issue("phone", "browser", {"chat:use"})
    now[0] += 300
    assert store.redeem(code) is None
    token = store.redeem(store.issue("phone", "browser", {"chat:use"}))
    now[0] += DAY
    assert store.authenticate(token, "browser") is None
    token = store.redeem(store.issue("phone", "browser", {"chat:use"}))
    for _ in range(13):
        now[0] += DAY / 2
        assert store.authenticate(token, "browser")
    now[0] += DAY / 2
    assert store.authenticate(token, "browser") is None
    token = store.redeem(store.issue("phone", "browser", {"chat:use"}))
    store.logout(store.authenticate(token, "browser"))
    assert store.authenticate(token, "browser") is None


def test_rotation_preserves_identity_recreation_does_not(credentials):
    store, now = credentials
    old = store.issue("phone", "shortcut", {"capture:create"})
    device = store.authenticate(old, "shortcut")
    new = store.issue("phone", "shortcut", {"capture:create"})
    assert store.authenticate(old, "shortcut") is None
    assert store.authenticate(new, "shortcut").device_id == device.device_id
    store.revoke("phone")
    assert store.authenticate(new, "shortcut") is None
    fresh = store.issue("phone", "shortcut", {"capture:create"})
    assert store.authenticate(fresh, "shortcut").device_id != device.device_id
    now[0] += 90 * DAY
    assert store.authenticate(fresh, "shortcut") is None


def test_restart_keeps_hashes_and_revocation_but_drops_onboarding(tmp_path):
    store = RemoteCredentials(tmp_path)
    token = store.issue("shortcut", "shortcut", {"capture:create"})
    revoked = store.issue("revoked", "shortcut", {"capture:create"})
    code = store.issue("phone", "browser", {"chat:use"})
    session = store.redeem(store.issue("paired", "browser", {"chat:use"}))
    setup = store.setup("android", "https://host.tail.ts.net")
    store.revoke("revoked")
    store.close()
    assert token.encode() not in (tmp_path / "devices.db").read_bytes()
    assert session.encode() not in (tmp_path / "devices.db").read_bytes()
    assert stat.S_IMODE((tmp_path / "devices.db").stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    store = RemoteCredentials(tmp_path)
    assert store.authenticate(token, "shortcut")
    assert store.authenticate(session, "browser")
    assert store.authenticate(revoked, "shortcut") is None
    assert store.redeem(code) is None
    assert store.download(setup.split("/")[-1][:-5], consume=True) is None
    store.close()


def test_legacy_import_is_capture_only_and_cannot_duplicate(credentials):
    store, now = credentials
    token = "ab" * 32
    assert store.issue("legacy", "shortcut", {"capture:create"}, legacy=token) == token
    now[0] += 200 * DAY
    assert store.authenticate(token, "shortcut").scopes == {"capture:create"}
    with pytest.raises(ValueError):
        store.issue("other", "shortcut", {"capture:create"}, legacy=token)
    with pytest.raises(ValueError):
        store.issue("legacy", "browser", {"chat:use"}, legacy=token)
    with pytest.raises(ValueError):
        store.issue("legacy", "shortcut", {"chat:use"})


@pytest.mark.parametrize("invalidate", ["expire", "cancel", "rotate", "revoke"])
def test_setup_invalidation(credentials, invalidate):
    store, now = credentials
    path = store.setup("android", "https://host.tail.ts.net")
    secret = path.rsplit("/", 1)[1][:-5]
    body = store.download(secret, consume=False)
    assert body["version"] == 90
    shortcut = body["categories"][0]["shortcuts"][0]
    assert shortcut["authentication"] == "bearer"
    assert store.authenticate(shortcut["authToken"], "shortcut").scopes == {"capture:create"}
    if invalidate == "expire": now[0] += 300
    if invalidate == "cancel": store.cancel_onboarding("android")
    if invalidate == "rotate": store.issue("android", "shortcut", {"capture:create"})
    if invalidate == "revoke": store.revoke("android")
    assert store.download(secret, consume=True) is None


def test_setup_download_once(credentials):
    store, _ = credentials
    secret = store.setup("android", "https://host.tail.ts.net").rsplit("/", 1)[1][:-5]
    assert store.download(secret, consume=True)
    assert store.download(secret, consume=True) is None


def test_live_tool_permissions_fail_closed(credentials):
    store, _ = credentials
    token = store.redeem(store.issue("phone", "browser", {"chat:use", "notes:read"}))
    device = store.authenticate(token, "browser")
    assert remote_tool_allowed(store, device, "read_note")
    assert remote_tool_allowed(store, device, "calculate")
    assert not remote_tool_allowed(store, device, "write_note")
    assert not remote_tool_allowed(store, device, "future_tool")
    store.revoke("phone")
    assert not remote_tool_allowed(store, device, "read_note")
    assert not remote_tool_allowed(store, device, None)


def test_transport_check_rejects_local_listener_funnel_and_overrides():
    status = {"BackendState": "Running", "Self": {"DNSName": "host.tail.ts.net."}}
    config = {"TCP": {"443": {"HTTPS": True}}, "Web": {"host.tail.ts.net:443": {
        "Handlers": {"/": {"Proxy": "http://127.0.0.1:27127"}}}}}
    assert check_state(status, config, "https://host.tail.ts.net") == []
    assert check_state(status, config, "https://host.tail.ts.net", 27125)
    config["AllowFunnel"] = {"host.tail.ts.net:443": True}
    assert "Funnel" in " ".join(check_state(status, config, "https://host.tail.ts.net"))
    config.pop("AllowFunnel")
    config["Foreground"] = {"session": {}}
    assert check_state(status, config, "https://host.tail.ts.net")


def test_mcp_harness_scope_guard_runs_before_discovery_or_dispatch():
    from core.providers.mcp_tools import MCPTools
    tools = object.__new__(MCPTools)
    tools.authorize_tool = lambda name: False
    # No tool sessions, loop or provider exists: any discovery/dispatch would fail.
    for name in ("read_note", "note_info", "write_note", "unknown"):
        result = tools.call_harness_tool(name, {})
        assert result.is_error and "permission" in result.text


@pytest.mark.parametrize("name,args", [("read_note", {"name": "secret"}), ("write_note", {"content": "no"})])
def test_session_scope_guard_precedes_write_checks(monkeypatch, name, args):
    from test_session import start_session, FakeMCPTools
    from test_tool_dispatch import TOOLS
    tools = FakeMCPTools(tools=TOOLS)
    session = start_session(monkeypatch, tools=tools)
    session.authorize_tool = lambda name: False
    monkeypatch.setattr(session, "_before_tool", lambda *a: pytest.fail("policy read happened before scope check"))
    result = session._call_tool("turn", name, args, None, None)
    assert result.code == "DEVICE_SCOPE" and result.certainty == "not_dispatched"
    assert not tools.calls
    session.close()


def test_request_quota_survives_rotation_and_expires(credentials):
    store, now = credentials
    token = store.issue("phone", "shortcut", {"capture:create"})
    device = store.authenticate(token, "shortcut")
    assert all(store.admit(device) for _ in range(120))
    assert not store.admit(device)
    token = store.issue("phone", "shortcut", {"capture:create"})
    device = store.authenticate(token, "shortcut")
    assert not store.admit(device)
    now[0] += 60
    assert store.admit(device)


@pytest.mark.parametrize("port", ["27125", "27126", "0", "65536"])
def test_cli_rejects_reserved_or_invalid_remote_ports(port):
    from core.service.server import main
    with pytest.raises(SystemExit) as error:
        main(["--remote-origin", "https://host.tail.ts.net", "--remote-port", port])
    assert error.value.code == 2


def test_human_pairing_code_normalization_and_secret_sizes(credentials):
    store, _ = credentials
    code = store.issue("phone", "browser", {"chat:use", "notes:read"})
    assert len(code) == 11 and code[5] == "-"
    assert set(code.replace("-", "")) <= set(PAIR_ALPHABET)
    token = store.redeem(code.lower().replace("-", " "))
    assert token and len(token) == 64
    assert store.redeem(code) is None
    assert len(store.issue("shortcut", "shortcut", {"capture:create"})) == 64


def test_pairing_failure_limit_is_global_atomic_and_does_not_consume_valid_code(credentials):
    store, now = credentials
    code = store.issue("phone", "browser", {"chat:use"})
    def attempt(_):
        try:
            assert store.redeem("invalid") is None
            return "failed"
        except PairingLimited as error:
            assert 1 <= error.retry_after <= 60
            return "limited"
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(attempt, range(50)))
    assert results.count("failed") == PAIR_FAILURE_LIMIT
    assert results.count("limited") == 50 - PAIR_FAILURE_LIMIT
    with pytest.raises(PairingLimited):
        store.redeem(code)
    now[0] += 60
    assert store.redeem(code)


def test_new_grants_and_successes_do_not_reset_pairing_failures(credentials):
    store, _ = credentials
    for _ in range(PAIR_FAILURE_LIMIT - 1):
        assert store.redeem("wrong") is None
    assert store.redeem(store.issue("phone", "browser", {"chat:use"}))
    assert store.redeem("wrong") is None
    code = store.issue("other", "browser", {"chat:use"})
    with pytest.raises(PairingLimited):
        store.redeem(code)


def test_device_list_explains_expiry_and_renewal(credentials):
    store, now = credentials
    store.issue("shortcut", "shortcut", {"capture:create"})
    store.issue("legacy", "shortcut", {"capture:create"}, legacy="ef" * 32)
    assert store.describe("shortcut")["status"] == "active"
    assert store.describe("legacy")["expires_at"] is None
    assert "no expiry" in store.describe("legacy")["renewal"]
    now[0] += 84 * DAY
    listing = {entry["name"]: entry for entry in store.list()}
    assert listing["shortcut"]["status"] == "expires_soon"
    assert listing["shortcut"]["expires_at"].endswith("+00:00")
    assert "update the shortcut" in listing["shortcut"]["renewal"]
    now[0] += 6 * DAY
    assert store.describe("shortcut")["status"] == "expired"
    assert store.describe("legacy")["status"] == "active"
    code = store.issue("phone", "browser", {"chat:use", "notes:read"})
    assert store.describe("phone")["status"] == "not_paired"
    assert store.describe("phone")["expires_at"] is None
    token = store.redeem(code)
    browser = store.describe("phone")
    assert browser["expires_at"] and browser["idle_expires_at"]
    assert "7 days" in browser["renewal"]
    store.issue("phone", "browser", {"chat:use"})
    from core.service.credentials import _hash
    assert store.describe("phone", digest=_hash(token)) == {}


def test_chat_only_cannot_start_inference_from_shared_history(credentials):
    store, _ = credentials
    token = store.redeem(store.issue("phone", "browser", {"chat:use"}))
    device = store.authenticate(token, "browser")
    assert not remote_tool_allowed(store, device, None)
    assert not remote_tool_allowed(store, device, "calculate")


@pytest.mark.parametrize("status", [401, 500, None])
def test_android_failure_script_explains_auth_and_ambiguous_results(status):
    import json
    import shutil
    import subprocess
    from core.service.android_setup import android_shortcut
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to execute the Android failure script")
    shortcut = android_shortcut("https://host.tail.ts.net", "ab" * 32)["categories"][0]["shortcuts"][0]
    harness = """const fs = require('node:fs'); const vm = require('node:vm');
const data = JSON.parse(fs.readFileSync(0, 'utf8')); const messages = [];
vm.runInNewContext(data.script, {response: data.response, alert: text => messages.push(text)});
process.stdout.write(JSON.stringify(messages));"""
    result = subprocess.run([node, "-e", harness], input=json.dumps({"script": shortcut["codeOnFailure"],
                            "response": {"statusCode": status} if status else None}), text=True,
                            capture_output=True, check=True, timeout=5)
    messages = json.loads(result.stdout)
    assert len(messages) == 1
    if status == 401:
        assert "reissue" in messages[0] and "update the shortcut token" in messages[0]
    else:
        assert "before retrying" in messages[0] and "cannot deduplicate" in messages[0]
