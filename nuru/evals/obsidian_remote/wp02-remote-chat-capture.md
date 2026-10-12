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
  plugin SHA-256 revision, legacy error codes, Android setup import shape,
  new request/status shapes, indistinguishable unknown/foreign status failures,
  and an explicit 23-case allow/deny permission matrix are checked by
  `nuru/tests/test_remote_contract.py`. The old receiver examples are derived
  from the pinned source and tests; this run did not operate a RemoteNotes host.
- PR #19 review refined the contract: Serve must target a separate remote-only
  listener rather than the WP01 local-token listener; capture ownership follows
  bounded body parsing; edge-case JSON/Unicode differences are explicit; Android
  setup keeps the legacy route and import fields. M2/M3 still have to implement
  these rules and test them over HTTP/private HTTPS.
- `git diff --check`: passed.
- `.venv/bin/python -m pytest -q nuru/tests/test_remote_contract.py nuru/tests/test_service_protocol.py nuru/tests/test_service_host.py nuru/tests/test_service_server.py`:
  **171 passed in 9.36 s**, without a local model and with loopback HTTP tests
  enabled.

M1 establishes the contract only. It does not claim duplicate-safe writes,
crash recovery, per-device HTTP enforcement, pairing, Tailscale access, phone
behavior or a live-model result. M2 and M3 must exercise the permission matrix
through the real service and prove capture recovery with process-level faults.
