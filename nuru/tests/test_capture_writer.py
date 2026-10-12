"""WP02 M2 capture tests: real scratch vault and SQLite, no model provider."""

import hashlib
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from core.service.capture import CaptureError, CaptureWriter
from core.service import capture
from core.service.store import ServiceStore


@pytest.fixture
def writer(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    store = ServiceStore(tmp_path / "service.db")
    result = CaptureWriter(store, vault, "vault-test", threading.RLock())
    yield result
    store.close()


def test_parallel_retries_and_body_fidelity(writer):
    payload = {"request_id": "phone-capture-0001", "content": "# idea\r\n'quoted' ☃\n---\n"}
    with ThreadPoolExecutor(max_workers=32) as pool:
        answers = list(pool.map(lambda _: writer.create("phone", payload), range(100)))
    completed = [body for status, body in answers if status == 201]
    assert completed and len({str(body) for body in completed}) == 1
    assert all(status in {201, 202} for status, _ in answers)
    receipt = completed[0]
    note = (writer.vault / receipt["path"]).read_bytes()
    assert note == f'---\nuid: "{receipt["uid"]}"\n---\n{payload["content"]}'.encode()
    assert receipt["revision"] == hashlib.sha256(note).hexdigest()
    assert len(list((writer.vault / "Inbox").glob("*.md"))) == 1
    assert writer.status("phone", payload["request_id"])[1]["receipt"] == receipt
    with pytest.raises(CaptureError) as error:
        writer.create("phone", {**payload, "content": "changed"})
    assert (error.value.status, error.value.code) == (409, "conflict")
    with pytest.raises(CaptureError) as foreign:
        writer.status("other-phone", payload["request_id"])
    assert (foreign.value.status, foreign.value.code) == (404, "not_found")


def test_inflight_duplicate_returns_status_without_waiting_for_write(writer, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = writer._publish

    def held(record, content):
        entered.set()
        assert release.wait(5)
        original(record, content)

    monkeypatch.setattr(writer, "_publish", held)
    payload = {"request_id": "phone-capture-0001", "content": "saved"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer.create, "phone", payload)
        assert entered.wait(5)
        status, running = writer.create("phone", payload)
        assert (status, running["state"]) == (202, "running")
        assert writer.status("phone", payload["request_id"])[1]["state"] == "running"
        release.set()
        receipt = future.result(timeout=5)[1]
    assert writer.status("phone", payload["request_id"])[1]["receipt"] == receipt


def test_shutdown_waits_for_accepted_capture_and_refuses_new_work(writer, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = writer._publish

    def held(record, content):
        entered.set()
        assert release.wait(5)
        original(record, content)

    monkeypatch.setattr(writer, "_publish", held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        capture_future = pool.submit(writer.create, "phone", {"request_id": "phone-capture-0001", "content": "saved"})
        assert entered.wait(5)
        assert not writer.stop(0)
        stop_future = pool.submit(writer.stop, 5)
        with pytest.raises(CaptureError) as stopped:
            writer.create("phone", {"request_id": "phone-capture-0002", "content": "next"})
        assert (stopped.value.status, stopped.value.code) == (503, "stopping")
        release.set()
        assert stop_future.result(timeout=5)
        assert capture_future.result(timeout=5)[0] == 201


def test_same_text_different_ids_and_legacy_requests_are_distinct(writer):
    for index in range(20):
        assert writer.create("phone", {"request_id": f"phone-capture-{index:04d}", "content": "same"})[0] == 201
    first = writer.create("phone", {"content": "same"})[1]
    second = writer.create("phone", {"content": "same"})[1]
    assert first["uid"] != second["uid"]
    assert len(list((writer.vault / "Inbox").glob("*.md"))) == 22


@pytest.mark.parametrize("phase,exit_code", [("before", 77), ("after", 78)])
def test_process_death_at_create_boundary_recovers_one_identity(tmp_path, phase, exit_code):
    vault = tmp_path / "vault"
    vault.mkdir()
    script = """
import os, sys, threading
from pathlib import Path
from core.service.capture import CaptureWriter
from core.service.store import ServiceStore
root = Path(sys.argv[1])
writer = CaptureWriter(ServiceStore(root / 'service.db'), root / 'vault', 'vault-test', threading.RLock())
original = writer._publish
def cut(record, content):
    if sys.argv[2] == 'before': os._exit(77)
    original(record, content)
    os._exit(78)
writer._publish = cut
writer.create('phone', {'request_id': 'phone-capture-0001', 'content': 'saved'})
"""
    process = subprocess.run([sys.executable, "-c", script, str(tmp_path), phase], check=False)
    assert process.returncode == exit_code
    store = ServiceStore(tmp_path / "service.db")
    writer = CaptureWriter(store, vault, "vault-test", threading.RLock())
    key = next(iter(store.pending_captures()))
    reserved = store.capture(key)
    writer.recover()
    status = writer.status("phone", "phone-capture-0001")[1]
    assert status["state"] == ("uncertain" if phase == "before" else "completed")
    retry = writer.create("phone", {"request_id": "phone-capture-0001", "content": "saved"})
    if phase == "before":
        assert retry[1]["state"] == "uncertain"
        assert not (vault / "Inbox").exists()
    else:
        assert retry[1]["uid"] == reserved["uid"]
        assert len(list((vault / "Inbox").glob("*.md"))) == 1
    store.close()


def test_process_death_after_reservation_can_retry_same_identity(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    script = """
import os, sys, threading
from pathlib import Path
from core.service.capture import CaptureWriter
from core.service.store import ServiceStore
root = Path(sys.argv[1])
store = ServiceStore(root / 'service.db')
writer = CaptureWriter(store, root / 'vault', 'vault-test', threading.RLock())
original = store.set_capture
def cut(key, state, receipt=None):
    if state == 'running': os._exit(79)
    original(key, state, receipt)
store.set_capture = cut
writer.create('phone', {'request_id': 'phone-capture-0001', 'content': 'saved'})
"""
    assert subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=False).returncode == 79
    store = ServiceStore(tmp_path / "service.db")
    writer = CaptureWriter(store, vault, "vault-test", threading.RLock())
    reserved = store.capture(store.pending_captures()[0])
    writer.recover()
    assert writer.status("phone", "phone-capture-0001")[1]["state"] == "accepted"
    receipt = writer.create("phone", {"request_id": "phone-capture-0001", "content": "saved"})[1]
    assert receipt["uid"] == reserved["uid"]
    assert len(list((vault / "Inbox").glob("*.md"))) == 1
    store.close()


def test_changed_target_is_uncertain_and_never_overwritten(writer):
    receipt = writer.create("phone", {"request_id": "phone-capture-0001", "content": "saved"})[1]
    key = next(iter(writer.store._conn.execute("SELECT key FROM captures")))[0]
    with writer.store.transaction():
        writer.store.set_capture(key, "running")
    path = writer.vault / receipt["path"]
    path.write_text("external edit")
    writer.recover()
    assert writer.status("phone", "phone-capture-0001")[1]["state"] == "uncertain"
    assert writer.create("phone", {"request_id": "phone-capture-0001", "content": "saved"})[1]["state"] == "uncertain"
    assert path.read_text() == "external edit"


def test_receipt_expiry_leaves_a_gone_tombstone(writer):
    payload = {"request_id": "phone-capture-0001", "content": "saved"}
    writer.create("phone", payload)
    old = (datetime.now(timezone.utc) - timedelta(days=31)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    writer.store._conn.execute("UPDATE captures SET completed_at = ?", (old,))
    writer.recover()
    assert writer.status("phone", payload["request_id"])[1]["state"] == "gone"
    with pytest.raises(CaptureError) as error:
        writer.create("phone", payload)
    assert (error.value.status, error.value.code) == (410, "gone")
    assert len(list((writer.vault / "Inbox").glob("*.md"))) == 1


def test_symlinked_inbox_cannot_escape_vault(writer, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (writer.vault / "Inbox").symlink_to(outside, target_is_directory=True)
    with pytest.raises(CaptureError):
        writer.create("phone", {"request_id": "phone-capture-0001", "content": "saved"})
    assert list(outside.iterdir()) == []


def test_capacity_and_quota_reject_new_work_but_allow_status_and_retries(writer, monkeypatch):
    payload = {"request_id": "phone-capture-0001", "content": "saved"}
    receipt = writer.create("phone", payload)[1]
    monkeypatch.setattr(capture, "MAX_RECORDS", 1)
    with pytest.raises(CaptureError) as full:
        writer.create("phone", {"request_id": "phone-capture-0002", "content": "new"})
    assert (full.value.status, full.value.code) == (503, "capture_capacity")
    assert writer.create("phone", payload)[1] == receipt
    assert writer.status("phone", payload["request_id"])[1]["receipt"] == receipt
    monkeypatch.setattr(capture, "MAX_RECORDS", 100_000)
    monkeypatch.setattr(capture, "PER_DEVICE_PER_MINUTE", 1)
    with pytest.raises(CaptureError) as quota:
        writer.create("phone", {"request_id": "phone-capture-0002", "content": "new"})
    assert (quota.value.status, quota.value.code) == (429, "capture_quota")


def test_existing_target_even_with_matching_bytes_is_not_claimed(writer, monkeypatch):
    uid = "01K7D5T2K00000000000000000"
    monkeypatch.setattr(capture, "ULID", lambda: uid)
    inbox = writer.vault / "Inbox"
    inbox.mkdir()
    target = inbox / f"{uid}.md"
    target.write_bytes(f'---\nuid: "{uid}"\n---\nsaved'.encode())
    with pytest.raises(CaptureError) as collision:
        writer.create("phone", {"request_id": "phone-capture-0001", "content": "saved"})
    assert collision.value.code == "capture_failed"
    assert writer.status("phone", "phone-capture-0001")[1]["state"] == "uncertain"
    assert target.read_bytes() == f'---\nuid: "{uid}"\n---\nsaved'.encode()
