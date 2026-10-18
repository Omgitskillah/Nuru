"""WP04 M1: a vault's UUID in <vault>/.nuru/vault-id, the registry that follows a moved vault and refuses
a copy until it's rebound or cloned, and protocol v2's status that carries it while v1 stays as it was."""

import json
import shutil
import threading
from pathlib import Path

import jsonschema
import pytest

from core import vault_identity
from core.conversation_store import ConversationStore
from core.service import protocol
from core.service.credentials import Credentials
from core.service.host import Service
from core.service.server import NuruServer, main
from core.service.store import ServiceStore
from core.vault_identity import ID_FILE, Registry, VaultConflict
from test_service_host import ScriptedSession, reply
from test_service_server import Client

V2_SCHEMA = json.loads((Path(protocol.__file__).with_name("protocol-v2.schema.json")).read_text())


@pytest.fixture
def registry(tmp_path):
    return Registry(tmp_path / "vaults")


def vault(tmp_path, name):
    folder = tmp_path / name
    folder.mkdir()
    return folder


def test_a_vault_gets_an_id_once_and_keeps_it(tmp_path, registry):
    notes = vault(tmp_path, "Notes")
    first = registry.identify(notes)
    assert (notes / ID_FILE).read_text() == first + "\n"
    assert registry.identify(notes) == first
    assert registry.entries() == {first: str(notes.resolve())}


def test_a_moved_vault_keeps_its_id(tmp_path, registry):
    notes = vault(tmp_path, "Notes")
    first = registry.identify(notes)
    moved = tmp_path / "Renamed"
    notes.rename(moved)
    assert registry.identify(moved) == first, "same ID, so the same history"
    assert registry.entries() == {first: str(moved.resolve())}


def test_a_copied_vault_is_refused_until_rebound_or_cloned(tmp_path, registry):
    notes = vault(tmp_path, "Notes")
    first = registry.identify(notes)
    copy = tmp_path / "Notes copy"
    shutil.copytree(notes, copy)
    with pytest.raises(VaultConflict) as refused:
        registry.identify(copy)
    assert refused.value.other == notes.resolve()
    assert f"vault rebind '{copy.resolve()}'" in str(refused.value), "a path with spaces, quoted to paste"
    assert f"vault clone '{copy.resolve()}'" in str(refused.value)

    assert registry.clone(copy) != first, "a separate vault: a new ID"
    assert registry.identify(copy) != first and registry.identify(notes) == first

    again = tmp_path / "Notes again"
    shutil.copytree(notes, again)
    assert registry.rebind(again) == first  # this copy is the one to keep
    assert registry.identify(again) == first
    with pytest.raises(VaultConflict):
        registry.identify(notes), "now the old one is the copy"


def test_a_file_that_isnt_an_id_is_never_overwritten(tmp_path, registry):
    notes = vault(tmp_path, "Notes")
    (notes / ".nuru").mkdir()
    (notes / ID_FILE).write_text("not an id\n")
    with pytest.raises(ValueError, match="isn't a Nuru vault ID"):
        registry.identify(notes)
    assert (notes / ID_FILE).read_text() == "not an id\n"


def test_vault_rebind_asks_first(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NURU_VAULTS_DIR", str(tmp_path / "vaults"))
    notes = vault(tmp_path, "Notes")
    first = Registry().identify(notes)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    main(["vault", "clone", str(notes)])
    assert vault_identity.read(notes) == first and "Nothing changed" in capsys.readouterr().out
    main(["vault", "clone", str(notes), "--yes"])
    assert vault_identity.read(notes) != first
    main(["vault", "list"])
    assert str(notes.resolve()) in capsys.readouterr().out


# --------------------------------------------------------------------------- protocol v2's status
@pytest.fixture
def served(tmp_path):
    credentials = Credentials(tmp_path / "service")
    token = credentials.add("obsidian")
    notes = vault(tmp_path, "Notes")
    vault_uuid = Registry(tmp_path / "vaults").identify(notes)
    service = Service(ServiceStore(tmp_path / "service.db"), ConversationStore(tmp_path / "conversation.db"),
                      lambda conversation_id: ScriptedSession([reply("hi")]), notes, vault_uuid=vault_uuid)
    server = NuruServer(service, credentials, port=0, keepalive_seconds=0.2)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield Client(server.server_port, token), service, vault_uuid
    server.closing = True
    service.close()
    server.shutdown()
    server.server_close()
    service.store.close()
    service.conversations.close()


def test_v2_status_names_the_vault_by_its_uuid(served):
    client, service, vault_uuid = served
    status, body, _ = client.call("GET", "/v2/status")
    assert status == 200 and body["v"] == 2
    assert body["vault"] == {"uuid": vault_uuid, "name": "Notes"}
    jsonschema.validate(body, {**V2_SCHEMA, "$ref": "#/$defs/status"})


def test_an_old_plugin_sees_v1_as_it_was(served):
    client, service, vault_uuid = served
    status, body, _ = client.call("GET", "/v1/status")
    assert status == 200 and body["v"] == 1
    assert body["vault"]["id"] == protocol.vault_id(service.vault), "still the path hash"
    assert vault_uuid not in json.dumps(body), "a v1 client never sees the UUID"
    protocol.validate("status", body)


def test_wrong_vault_is_a_v2_error_only():
    v1 = json.loads(protocol.SCHEMA_PATH.read_text())["$defs"]["error"]["properties"]["error"]["properties"]["code"]["enum"]
    v2 = V2_SCHEMA["$defs"]["error"]["properties"]["error"]["properties"]["code"]["enum"]
    assert "wrong_vault" not in v1 and set(v2) == set(v1) | {"wrong_vault"}
    assert set(protocol.ERRORS_V2) == set(v2) and protocol.ERRORS_V2["wrong_vault"] == 409
    assert "wrong_vault" not in protocol.ERRORS
