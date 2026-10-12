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
- A follow-up review found 27126 is already used by the manual test servers. The
  planned remote-only listener now uses 27127, and the WP02 plan matches.
- `git diff --check`: passed.
- `.venv/bin/python -m pytest -q nuru/tests/test_remote_contract.py nuru/tests/test_service_protocol.py nuru/tests/test_service_host.py nuru/tests/test_service_server.py`:
  **171 passed in 9.36 s**, without a local model and with loopback HTTP tests
  enabled.

M1 established the contract only. At that point, duplicate-safe writes, crash
recovery, per-device HTTP enforcement, pairing, Tailscale access, phone behavior
and live-model results were not yet claimed. M2 covers capture recovery below;
M3 still needs the remote permission checks.

## M2: direct capture and recovery (2026-10-12)

**Status:** backend writer and local HTTP route implemented on
`wp02-m2-direct-capture`. Remote credential enforcement and Tailscale exposure
remain M3.

- `POST /v1/captures` accepts the M1 legacy and request-ID forms on the existing
  loopback service. `GET /v1/captures/requests/{request_id}` returns the requesting
  client's state. The credential's server-side client name supplies device
  identity; caller-supplied identity, vault and path are not accepted. The remote
  listener and capture-only credentials are not enabled yet.
- A SQLite row commits the device/vault-scoped request key, canonical content
  hash, ULID, Inbox path and exact note-byte hash before publication. The writer
  stages and fsyncs the note, hard-links it to the final path without clobbering,
  and fsyncs the directory. The staged inode proves provenance if the process
  dies after publication but before the receipt commits. The original SHA-256
  revision remains in the immutable receipt even after external edits.
- Recovery distinguishes a reserved request from one whose publication began.
  A reserved request with no file can retry its original UID. If publication
  began and the final file is missing or differs, the status is `uncertain`:
  another editor could have moved or changed it. It is never replayed
  automatically. A matching final file and staged inode recover the original
  receipt. A pre-existing target, even with matching bytes, is not claimed.
- Full receipts are retained for 30 days and compacted to permanent ID/hash
  tombstones on startup. The ledger keeps at most 100,000 requests, accepts at
  most 60 new captures per device per rolling minute and runs at most eight
  writers concurrently. New work gets 503 `capture_capacity`, 429
  `capture_quota` or 429 `capture_queue_full` rather than evicting safety
  records. Uploads are limited to 256 KiB and five seconds. Status and same-ID
  retries remain available when new work reaches a limit.
- The model-free suite uses scratch vaults and SQLite, 100 parallel same-ID
  calls, 20 distinct IDs with identical text, a real loopback HTTP listener and
  a disconnected client whose original receipt is recovered by status/retry,
  changed-body conflicts, foreign-device status, symlink and pre-existing-path
  refusals, retention and quota cases. Separate subprocesses terminate at
  reservation, before publication and after publication; restart verifies one
  reserved identity, a safe retry or an explicit uncertain state, and no second
  note. A held writer proves duplicate/status calls remain responsive, while
  graceful shutdown waits for accepted work. The HTTP test verifies no session
  factory is called. No local model or provider is started.
- `.venv/bin/python -m pytest -q nuru/tests/test_capture_writer.py
  nuru/tests/test_service_server.py nuru/tests/test_remote_contract.py
  nuru/tests/test_service_host.py nuru/tests/test_service_protocol.py`:
  **187 passed** without a local model.

M3 still must enforce scoped credentials, validate the private listener and
HTTPS route, and test migration and rollback. M4 must ship request-aware
shortcuts, so installed legacy shortcuts remain single-shot until updated.
