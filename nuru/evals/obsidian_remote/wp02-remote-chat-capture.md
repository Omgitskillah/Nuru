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
  device's state. New local credentials carry a stable server-assigned device ID;
  pre-M2 credentials retain their client name as the ID for ledger continuity.
  Rotation keeps the ID, but revoke and recreate does not inherit the old
  device's history. Caller-supplied identity, vault and path are not accepted. The remote
  listener and capture-only credentials are not enabled yet.
- A SQLite row commits the device/vault-scoped request key, canonical content
  hash, ULID, Inbox path and exact note-byte hash before publication. The writer
  stages and fsyncs the note, hard-links it to the final path without clobbering,
  and fsyncs the directory. The staged inode proves provenance if the process
  dies after publication but before the receipt commits. The original SHA-256
  revision remains in the immutable receipt even after external edits.
- Recovery distinguishes a reserved or staging request from one whose publication began.
  A reserved request or a torn stage with no final file can retry its original
  UID after the unpublished stage is removed. A staging write/fsync failure
  follows the same rule, preserving its committed phase during reconciliation. If publication
  began and the final file is missing or differs, the status is `uncertain`:
  another editor could have moved or changed it. It is never replayed
  automatically. A matching final file and staged inode recover the original
  receipt. A pre-existing target, even with matching bytes, is not claimed.
- Full receipts are retained for 30 days and compacted to permanent ID/hash
  tombstones on startup and on active traffic at most hourly. Completed legacy
  single-shot rows remain for the one-minute per-device quota window, then are
  deleted by the next sweep because they carry no client-reusable ID. The
  ledger keeps at most 100,000 request-aware records overall and 20,000 per
  device; `NURU_CAPTURE_MAX_RECORDS` and `NURU_CAPTURE_MAX_DEVICE_RECORDS`
  can raise these limits after a ledger backup. If a cap is reached, stop the
  service, back up the `service.db` sibling of `NURU_DB_PATH` (or the default
  `nuru/.db/service.db`), raise the relevant limit, and restart. Do not delete
  capture tombstones: that would allow old IDs to create another note. It accepts at
  most 60 new captures per device per rolling minute and runs at most eight
  writers concurrently. New work gets 503 `capture_capacity`, 429
  `capture_quota` or 429 `capture_queue_full` rather than evicting safety
  records. Uploads are limited to 256 KiB and five seconds. Status and same-ID
  retries remain available when new work reaches a limit.
- Service startup probes hard-link support in Inbox. On an unsupported vault
  filesystem, new captures fail with 503 `capture_unavailable` before reserving
  an ID; chat and existing capture status still work. Operators must use a
  supported vault filesystem before routing old shortcuts to this receiver.
  Chunked upload is currently rejected, unlike the pinned plugin; M3 migration
  and M4 device acceptance must check the installed and shipped shortcuts.
- The model-free suite uses scratch vaults and SQLite, 100 parallel same-ID
  calls, 20 distinct IDs with identical text, a real loopback HTTP listener and
  a disconnected client whose original receipt is recovered by status/retry,
  changed-body conflicts, foreign-device status, symlink and pre-existing-path
  refusals, retention and quota cases. Separate subprocesses terminate at
  reservation, during staging, before publication and after publication; restart verifies one
  reserved identity, a safe retry or an explicit uncertain state, and no second
  note. A held writer proves duplicate/status calls remain responsive, while
  graceful shutdown waits for accepted work. In-flight GET status agrees with
  duplicate POST that the writer is `running`. A 61st legacy capture in one
  minute is rejected, and an injected staging fsync failure safely retries the
  original UID. The HTTP test verifies no session
  factory is called. No local model or provider is started.
- `.venv/bin/python -m pytest -q nuru/tests/test_capture_writer.py
  nuru/tests/test_service_server.py nuru/tests/test_remote_contract.py
  nuru/tests/test_service_host.py nuru/tests/test_service_protocol.py`:
  **196 passed** without a local model after the PR #20 review fixes.

M3 still must enforce scoped credentials, validate the private listener and
HTTPS route, bound accepted connections before private exposure, and test
migration and rollback. M4 must ship request-aware
shortcuts, so installed legacy shortcuts remain single-shot until updated.

## M3: scoped devices and private listener (2026-10-13)

**Status:** implemented for review on `wp02-m3-pairing-private-access`, based on
merged M2 (`b4c79b2`). The real private HTTPS/cutover gate remains **incomplete**.
[Operator and rollback guide](../../docs/obsidian-remote/wp02-m3-private-access.md).

Implemented and covered by model-free tests:

- An opt-in remote-only listener on 127.0.0.1:27127, sharing the host and capture
  ledger with WP01. Local tokens and remote credentials fail on the other listener,
  even with forged forwarding headers. Remote status omits vault identity/name.
- Host-local device approval, capture-only bearer issuance/import, rotation,
  revocation, and hashed persistent storage. Rotation retains capture ownership;
  revoking and recreating a name does not expose prior receipts. Imports cannot
  duplicate another device's token or gain chat authority.
- Single-use, five-minute pairing, Secure/HttpOnly/SameSite cookies, idle/absolute
  expiry, session-bound CSRF, explicit Origin/Host checks, logout, and a minimal
  same-origin pairing page. Session secrets never enter localStorage or URLs.
- Legacy Android schema-90 setup: pinned RemoteNotes source attribution, one-time
  five-minute download, headers, wrong-method handling and invalidation on restart,
  cancellation, rotation or revocation. M4 still owns request-aware templates.
- Authentication and scope checks before body reads, ownership preflight for URL
  resources, bounded headers/uploads/connections, per-device API quotas, and
  revocation rechecks after upload and during streams. Code-issued write approvals
  require write scope. Both ordinary and harness tool dispatch enforce live scopes;
  denied queued turns do not initialize the model.
- A disposable HTTP proxy rehearsal switches from a frozen legacy responder to
  Nuru, rolls back, then switches forward. The legacy payload still works, authored
  bytes stay unchanged, and the original request-ID receipt replays without a new
  note. This is not evidence of a live old receiver or Tailscale migration.

Validation notes:

- Initial tests without loopback permissions failed at socket bind; rerunning with
  loopback enabled passed. A socket-capacity test was corrected to accept both EOF
  and TCP reset as bounded refusal, as macOS returns reset on excess connections.
- The initial combined model-free suite passed **206 tests in 45.15 s**. Final
  protocol/authorization regression results are recorded below.
- Tailscale CLI **1.102.4-t3caf7d9e7-g084ee3b64** reports a running node. Read-only
  inspection found private HTTPS port 443 targeting `http://127.0.0.1:27124`, with
  no enabled Funnel. No route, receiver, installed shortcut, or personal vault
  was changed. Full status output and hostname are omitted from committed evidence.
- Running the new `remote-check` against that actual origin exited **2**, correctly
  reporting that Serve must target the remote listener. The command performs only
  `status --json` and `serve status --json`; it never mutates Tailscale configuration.

Still required before ticking M3: operator-approved actual HTTPS cutover against a
scratch vault, invalid/revoked rejection over that route, actual legacy receiver
write drain and rollback, and installed-client Content-Length compatibility.
Physical phone/chat acceptance remains M4. All live-model tests remain deferred
as requested; none were run for this milestone.

TODO justification: M3 changes from open to **in progress**, because the code and
model-free checks are reviewable but the original real private HTTPS exit gate
has not passed. The gate is retained, not weakened or marked complete. The main
WP02 plan links the operator guide and states this remaining gate explicitly.

Final regression command:

```sh
.venv/bin/python -m pytest \
  nuru/tests/test_remote_credentials.py nuru/tests/test_remote_server.py \
  nuru/tests/test_service_server.py nuru/tests/test_service_host.py \
  nuru/tests/test_service_protocol.py nuru/tests/test_remote_contract.py \
  nuru/tests/test_capture_writer.py nuru/tests/test_tool_dispatch.py \
  nuru/tests/test_mcp_tools.py -q
```

**288 passed in 48.32 s**. `node --check nuru/src/core/service/pair.js` and
`git diff --check` passed. A subsequent targeted check covers reserved/invalid
remote CLI ports and prevents the two credential boundaries sharing a listener.
The targeted credential/CLI rerun passed **20 tests in 0.18 s**, including all
four reserved/invalid port cases added after the combined suite.

Final HTTP compatibility review added HEAD/OPTIONS/PUT/PATCH/DELETE handling for
Android setup links: each returns 405 without consuming a live download; HEAD
sends no response body. The remote HTTP suite then passed **26 tests in 27.89 s**.
