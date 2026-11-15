"""Cross-process F40 compute ownership. No automatic expiry or stale-lock deletion."""
import json
import os
import secrets

import psutil
from datetime import datetime, timezone
from pathlib import Path

from core import user_models
from core.context_memory import Unsupported


class ComputeLease:
    def __init__(self, model, directory=None):
        self.directory = Path(directory) if directory is not None else Path(os.environ.get("NURU_SHARED_COMPUTE_DIR") or user_models.models_dir().parent / "shared-compute")
        self.path = self.directory / "local-model.lock"
        self.model = model; self.run = secrets.token_hex(16); self.retained = False

    def __enter__(self):
        if self.directory.is_symlink():
            raise Unsupported("Compute reservation directory cannot be a symbolic link")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.path.mkdir(mode=0o700)
        except FileExistsError:
            raise Unsupported("Local model compute is reserved; wait for its owner to finish") from None
        self.owner = {"owner": "Nuru F40", "run_id": self.run, "pid": os.getpid(), "process_created": psutil.Process().create_time(), "models": [self.model],
                      "purpose": "Explicit context-memory measurement or reload",
                      "expected_duration": "Up to four bounded loads plus restore; no expiry on uncertainty", "acquired_at": datetime.now(timezone.utc).isoformat()}
        # An interrupted metadata write intentionally leaves the reservation occupied.
        with (self.path / "owner.json").open("x", encoding="utf-8") as file:
            json.dump(self.owner, file); file.flush(); os.fsync(file.fileno())
        return self

    def retain(self, reason):
        self.retained = True
        self.owner["uncertain"] = reason
        user_models._write(self.path / "owner.json", json.dumps(self.owner))

    def __exit__(self, *args):
        if self.retained:
            return
        try:
            owner = json.loads((self.path / "owner.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Unsupported("Compute ownership could not be verified; the reservation was preserved") from None
        if owner.get("run_id") != self.run:
            raise Unsupported("Compute ownership changed; the reservation was preserved")
        (self.path / "owner.json").unlink()
        self.path.rmdir()  # never recursively deletes another contributor's files
