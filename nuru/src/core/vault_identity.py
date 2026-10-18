"""A vault's identity (WP04 M1): a random UUID in <vault>/.nuru/vault-id, so it survives the vault being
moved or renamed, unlike the path hash protocol v1 uses (protocol.vault_id). Each computer keeps a
registry of where it last saw each UUID (~/.nuru/vaults/registry.json, or NURU_VAULTS_DIR).

A vault whose UUID is registered at another path that still exists and still holds that UUID is a copy:
it isn't served until the user says which it is (rebind: the same vault, the other copy is stale; clone:
a new vault with its own UUID). A UUID whose old path is gone or holds another ID was moved, and the
registry just follows it.

The file is created by the service the first time it serves a vault, and read by the Obsidian plugin.
"""

import json
import os
import shlex
import uuid as uuidlib
from pathlib import Path

ID_FILE = Path(".nuru") / "vault-id"


def vaults_dir() -> Path:
    return Path(os.environ.get("NURU_VAULTS_DIR") or Path.home() / ".nuru" / "vaults")


class VaultConflict(RuntimeError):
    """The vault's UUID belongs to another live copy of it."""

    def __init__(self, vault: Path, vault_uuid: str, other: Path) -> None:
        quoted = shlex.quote(str(vault))
        super().__init__(
            f"{vault} has the same Nuru vault ID as {other}, which still exists: one is a copy of the other. "
            f"If {vault} is the one to keep (the other is an old copy), run `uv run nuru-serve vault rebind {quoted}`; "
            f"if it's a separate vault, run `uv run nuru-serve vault clone {quoted}` to give it its own ID and history.")
        self.vault, self.uuid, self.other = vault, vault_uuid, other


def read(vault: Path) -> str | None:
    """The vault's UUID, or None if it has none (never served) or the file isn't a UUID."""
    try:
        text = (Path(vault) / ID_FILE).read_text().strip()
        return str(uuidlib.UUID(text)) if len(text) == 36 else None
    except (OSError, ValueError):
        return None


def _write(vault: Path, vault_uuid: str, *, replace: bool) -> None:
    path = Path(vault) / ID_FILE
    path.parent.mkdir(exist_ok=True)
    if replace:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(vault_uuid + "\n")
        os.replace(temporary, path)
        return
    with open(path, "x") as file:  # never over an ID another process has just written
        file.write(vault_uuid + "\n")


class Registry:
    def __init__(self, directory: Path | None = None) -> None:
        self.path = Path(directory or vaults_dir()) / "registry.json"

    def entries(self) -> dict[str, str]:
        """{uuid: last-seen path}."""
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}

    def _save(self, entries: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, self.path)

    def identify(self, vault: Path) -> str:
        """The vault's UUID, made if it has none, after checking it isn't a copy (VaultConflict)."""
        vault = Path(vault).resolve()
        vault_uuid = read(vault)
        if vault_uuid is None:
            try:
                _write(vault, str(uuidlib.uuid4()), replace=False)
            except FileExistsError:
                pass  # another process made it first, or the file isn't a UUID: read decides
            vault_uuid = read(vault)
            if vault_uuid is None:
                raise ValueError(f"{vault / ID_FILE} isn't a Nuru vault ID; remove it to make a new one")
        entries = self.entries()
        known = entries.get(vault_uuid)
        if known and Path(known) != vault and read(Path(known)) == vault_uuid:
            raise VaultConflict(vault, vault_uuid, Path(known))
        if known != str(vault):
            entries[vault_uuid] = str(vault)  # first seen, or moved
            self._save(entries)
        return vault_uuid

    def rebind(self, vault: Path) -> str:
        """`vault` keeps its UUID (and so its history); the other copy is the stale one."""
        vault = Path(vault).resolve()
        vault_uuid = read(vault)
        if vault_uuid is None:
            raise ValueError(f"{vault} has no Nuru vault ID")
        entries = self.entries()
        entries[vault_uuid] = str(vault)
        self._save(entries)
        return vault_uuid

    def clone(self, vault: Path) -> str:
        """`vault` gets a new UUID: a separate vault, with an empty history."""
        vault = Path(vault).resolve()
        vault_uuid = str(uuidlib.uuid4())
        _write(vault, vault_uuid, replace=True)
        entries = self.entries()
        entries[vault_uuid] = str(vault)
        self._save(entries)
        return vault_uuid
