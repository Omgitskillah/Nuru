# WP02 remote chat and capture evidence

## M1: contract and migration (2026-10-12)

**Status:** contract and model-free fixtures implemented on
`wp02-m1-remote-contract`; backend capture and remote authentication are M2/M3.

- Source pins: RemoteNotes Obsidian receiver
  `04a2774ba9c72cd2599d7147822d51bb26b2fae6` and Rust receiver
  `046fe9a8b6a0c59693f055f7bb35c9eb66ea85b7`.
- Contract: [`wp02-m1-contract.md`](../../docs/obsidian-remote/wp02-m1-contract.md).
  Fixtures: [`wp02_capture_v1.json`](../../tests/fixtures/wp02_capture_v1.json).
- Old content-only request and four-field 201 receipt, both legacy note envelopes,
  plugin SHA-256 revision, legacy error codes, new request/status shapes, and an
  explicit 23-case allow/deny permission matrix are checked by
  `nuru/tests/test_remote_contract.py`. The old receiver examples are derived
  from the pinned source and tests; this run did not operate a RemoteNotes host.
- `git diff --check`: passed.
- `.venv/bin/python -m pytest -q nuru/tests/test_remote_contract.py nuru/tests/test_service_protocol.py nuru/tests/test_service_host.py nuru/tests/test_service_server.py`:
  **166 passed in 8.25 s**, without a local model. The first sandboxed run
  passed 158 tests but could not bind `127.0.0.1` for eight server cases; an
  allowed loopback rerun passed all 166 with no code change.

M1 establishes the contract only. It does not claim duplicate-safe writes,
crash recovery, per-device HTTP enforcement, pairing, Tailscale access, phone
behavior or a live-model result. M2 and M3 must exercise the permission matrix
through the real service and prove capture recovery with process-level faults.
