import json
import os
import subprocess
import sys
import time

import pytest

from core.service import credentials
from core.service.credentials import Credentials

# An add in its own process that stops between reading tokens.json and writing it back, until told to go.
_SLOW_ADD = """
import pathlib, sys, time
from core.service.credentials import Credentials
directory, read, go = (pathlib.Path(arg) for arg in sys.argv[1:4])
creds = Credentials(directory)
snapshot = creds._clients
def paused():
    clients = snapshot()
    read.touch()
    deadline = time.monotonic() + 10
    while not go.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    return clients
creds._clients = paused
print(creds.add("new"))
"""

_REMOVE = """
import pathlib, sys
from core.service.credentials import Credentials
print(Credentials(pathlib.Path(sys.argv[1])).remove("old"))
"""


def _python(script, *args):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    return subprocess.Popen([sys.executable, "-c", script, *map(str, args)], stdout=subprocess.PIPE, text=True, env=env)


def _wait_for(path, seconds=10):
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, f"{path.name} never appeared"
        time.sleep(0.01)


def test_a_revoke_between_another_processs_read_and_write_stays_revoked(tmp_path):
    """F03: an add that read tokens.json before a revoke must not write the revoked hash back."""
    directory = tmp_path / "service"
    old = Credentials(directory).add("old")
    kept = Credentials(directory).add("kept")
    read, go = tmp_path / "read", tmp_path / "go"

    adder = _python(_SLOW_ADD, directory, read, go)
    _wait_for(read)
    remover = _python(_REMOVE, directory)
    with pytest.raises(subprocess.TimeoutExpired):
        remover.wait(0.5)  # the revoke waits for the add to finish instead of racing it
    go.touch()
    new = adder.communicate(timeout=20)[0].strip()
    assert remover.communicate(timeout=20)[0].strip() == "True"
    assert adder.returncode == 0 and remover.returncode == 0

    creds = Credentials(directory)
    assert creds.check(old) is None
    assert creds.check(new) == ("operator", "new") and creds.check(kept) == ("operator", "kept")
    assert creds.clients() == ["kept", "new"]


def test_concurrent_adds_in_separate_processes_all_survive(tmp_path):
    directory = tmp_path / "service"
    script = "import pathlib, sys\nfrom core.service.credentials import Credentials\n" \
             "print(Credentials(pathlib.Path(sys.argv[1])).add(sys.argv[2]))"
    adders = {f"c{n}": _python(script, directory, f"c{n}") for n in range(6)}
    tokens = {name: process.communicate(timeout=30)[0].strip() for name, process in adders.items()}

    creds = Credentials(directory)
    assert creds.clients() == sorted(tokens)
    assert all(creds.check(token) == ("operator", name) for name, token in tokens.items())


def test_a_write_never_touches_another_writers_staging_file(tmp_path):
    directory = tmp_path / "service"
    directory.mkdir()
    staging = directory / "tokens.json.tmp"  # the fixed name every writer used to share
    staging.write_text("another writer's half-written file")

    token = Credentials(directory).add("phone")

    assert staging.read_text() == "another writer's half-written file"
    assert Credentials(directory).check(token) == ("operator", "phone")


def test_a_failed_write_leaves_the_old_file_and_no_staging_file(tmp_path, monkeypatch):
    directory = tmp_path / "service"
    creds = Credentials(directory)
    token = creds.add("phone")
    before = (directory / "tokens.json").read_text()

    def refuse(source, target):
        raise OSError("disk full")

    monkeypatch.setattr(credentials.os, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        creds.remove("phone")

    assert (directory / "tokens.json").read_text() == before and creds.check(token) == ("operator", "phone")
    assert not list(directory.rglob("*.tmp"))


@pytest.mark.skipif(sys.platform == "win32", reason="Windows file modes have no group or other bits")
def test_the_lock_and_every_written_file_are_private(tmp_path):
    directory = tmp_path / "service"
    Credentials(directory).add("phone")

    for path in (directory / "tokens.json", directory / "tokens.lock", directory / "clients" / "phone.token"):
        assert path.stat().st_mode & 0o077 == 0, path.name
    assert json.loads((directory / "tokens.json").read_text())["clients"]["phone"]["principal"] == "operator"


def test_removing_a_client_that_was_never_added_changes_nothing(tmp_path):
    directory = tmp_path / "service"
    token = Credentials(directory).add("phone")

    assert Credentials(directory).remove("tablet") is False
    assert Credentials(directory).check(token) == ("operator", "phone")
