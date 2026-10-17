"""nuru-serve's client credentials, outside the vault under ~/.nuru/service/ (NURU_SERVICE_DIR in
tests): the directory 0700, every file 0600.

`tokens.json` holds only a hash of each client's token, with the principal and client it names. A
local client reads its own token from `clients/<name>.token`, written once when the client is added.
WP01 has one principal, the operator. WP02 remote devices use a separate registry
and never authenticate with these local-only credentials.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

PRINCIPAL = "operator"
_CLIENT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def service_dir() -> Path:
    return Path(os.environ.get("NURU_SERVICE_DIR") or Path.home() / ".nuru" / "service")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _write_private(path: Path, text: str) -> None:
    """Replace `path` with `text`, never readable by others, not even for a moment."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    temporary.unlink(missing_ok=True)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(text)
    os.replace(temporary, path)


class Credentials:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory or service_dir())
        self._file = self.directory / "tokens.json"

    def _clients(self) -> dict:
        try:
            return json.loads(self._file.read_text())["clients"]
        except FileNotFoundError:
            return {}

    def clients(self) -> list[str]:
        return sorted(self._clients())

    def add(self, client: str) -> str:
        """A new token for `client` (replacing any it had), stored as a hash; the token itself goes to
        clients/<client>.token for the local client to read. Returns the token."""
        if not _CLIENT_NAME.match(client):
            raise ValueError("a client name is 1-32 lowercase letters, digits, - or _")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        token = secrets.token_urlsafe(32)
        clients = self._clients()
        # Rotation keeps the identity; removing and recreating the name does not
        # expose the former device's capture history.
        device_id = clients[client].get("device_id", client) if client in clients else f"local-{secrets.token_hex(16)}"
        clients[client] = {"principal": PRINCIPAL, "hash": _hash(token), "device_id": device_id,
                           "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _write_private(self.directory / "clients" / f"{client}.token", token)
        _write_private(self._file, json.dumps({"clients": clients}, indent=1))
        return token

    def remove(self, client: str) -> bool:
        """Revoke `client`'s token. False if there was no such client."""
        clients = self._clients()
        if clients.pop(client, None) is None:
            return False
        _write_private(self._file, json.dumps({"clients": clients}, indent=1))
        (self.directory / "clients" / f"{client}.token").unlink(missing_ok=True)
        return True

    def check(self, token: str) -> tuple[str, str] | None:
        """(principal, client) for a valid token, else None. Every stored hash is compared, in
        constant time, so the time taken doesn't depend on which one matches."""
        found = self.check_device(token)
        return found[:2] if found else None

    def check_device(self, token: str) -> tuple[str, str, str] | None:
        """Return principal, display name and stable device ID for a valid token."""
        found, given = None, _hash(token)
        for client, entry in self._clients().items():
            if hmac.compare_digest(entry["hash"], given):
                found = (entry["principal"], client, entry.get("device_id", client))
        return found
