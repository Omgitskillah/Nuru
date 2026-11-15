"""Explicit operator recovery; elapsed time and /api/ps never prove quiescence."""
import json
import logging
import math
import stat

import psutil

from core.context_lease import ComputeLease
from core.context_memory import Unsupported


def _snapshot(directory=None):
    lease = ComputeLease("recovery", directory)
    if lease.directory.is_symlink() or lease.path.is_symlink():
        raise Unsupported("Reservation path is a symbolic link; preserved for inspection")
    try:
        identity = lease.path.stat()
    except FileNotFoundError:
        return lease, None, None, None
    path = lease.path / "owner.json"
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 8192:
        raise Unsupported("Reservation metadata is unsafe; preserved for inspection")
    data = path.read_bytes()
    owner = json.loads(data)
    if not isinstance(owner, dict) or owner.get("owner") != "Nuru F40":
        raise Unsupported("This reservation belongs to another tool; preserved")
    run = owner.get("run_id")
    pid = owner.get("pid")
    created = owner.get("process_created")
    if not isinstance(run, str) or len(run) != 32 or any(c not in "0123456789abcdef" for c in run):
        raise Unsupported("Reservation identity is incomplete; preserved")
    if type(pid) is not int or pid <= 0:
        raise Unsupported("Reservation process identity is incomplete; preserved")
    if created is not None and (type(created) not in (int, float) or not math.isfinite(created) or created <= 0):
        raise Unsupported("Reservation process identity is invalid; preserved")
    try:
        process = psutil.Process(pid)
        # Missing legacy creation time is conservatively live if its PID exists.
        # Small clock/boot-time rounding differences must not declare a live owner dead.
        alive = created is None or abs(process.create_time() - created) <= 1.0
    except psutil.NoSuchProcess:
        alive = False
    except psutil.AccessDenied:
        raise Unsupported("Cannot check the reservation owner; preserved") from None
    key = (identity.st_dev, identity.st_ino, data)
    return lease, owner, alive, key


def inspect(directory=None):
    _, owner, alive, _ = _snapshot(directory)
    if owner is None:
        return {"reserved": False}
    return {"reserved": True, "run_id": owner["run_id"], "owner_running": alive,
            "state": "abandoned" if not alive else "uncertain" if "uncertain" in owner else "busy",
            "recovery": "Stop the owning Nuru service, restart Ollama, then release this exact run ID explicitly."}


def release(expected_run, *, ollama_restarted=False, directory=None):
    """Operator must restart Ollama, stopping any queued/in-flight server operation."""
    if ollama_restarted is not True:
        raise Unsupported("Restart Ollama first and explicitly confirm it; no reservation was released")
    lease, owner, alive, key = _snapshot(directory)
    if owner is None or owner["run_id"] != expected_run:
        raise Unsupported("Reservation changed or is absent; inspect again")
    if alive:
        raise Unsupported("The reservation owner is still running; stop it before recovery")
    # Never remove extra files or follow another writer's replacement directory.
    if {p.name for p in lease.path.iterdir()} != {"owner.json"}:
        raise Unsupported("Reservation contains additional files; preserved")
    _, latest, running, latest_key = _snapshot(directory)
    if key != latest_key or running or latest["run_id"] != expected_run:
        raise Unsupported("Reservation changed during recovery; preserved")
    logging.getLogger(__name__).warning("Operator confirmed Ollama restart; releasing dead owner run=%s pid=%s", expected_run, owner["pid"])
    (lease.path / "owner.json").unlink()
    lease.path.rmdir()
    return {"released": expected_run}
