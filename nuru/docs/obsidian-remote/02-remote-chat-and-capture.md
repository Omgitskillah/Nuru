# WP02 — Remote Nuru chat and reliable note capture

Status: in progress, 2026-10-13 (M1 and M2 implemented; M3 implementation under review,
real private HTTPS gate pending). See the [M3 operator guide](wp02-m3-private-access.md). All thresholds below are acceptance targets, not
measurements. Depends on [WP01 local service and plugin](01-local-obsidian-chat.md).
The same service later exposes [WP03 maintenance reports](03-vault-maintenance-ui.md).

## Outcome and user workflow

From an iPhone or Android browser, connect privately to Nuru on the Mac, resume a
conversation, stream a reply, answer an approval request and save a note. Existing
Siri/HTTP Shortcuts can still capture verbatim text to Inbox without invoking a
model. Obsidian can be closed; the Mac and Nuru service must remain available.
A lost response must not make a new client repeat a successful capture or write.

Host support is macOS. Client acceptance covers Safari on iPhone and Chrome on
Android. This does not require an Obsidian mobile plugin, a native phone app,
Signal integration, Windows hosting, public Internet exposure or multi-user SaaS.
There is no requirement to keep generating in a backgrounded phone browser.

## Reuse and migration boundary

RemoteNotes contains a standalone Rust capture service on main and an Obsidian
capture plugin on `feat/obsidian-background-capture`:
[plugin documentation](https://github.com/Omgitskillah/RemoteNotes/blob/feat/obsidian-background-capture/obsidian-plugin/README.md).
The plugin already has Tailscale status checks, token rotation, QR onboarding and
phone setup guidance. Its HTTP receiver writes through Obsidian and stops when
Obsidian closes. Current captures are not deduplicated: a retry creates another
note. The Rust and Python note schemas/history guarantees also differ.

Port reusable onboarding/client code with a pinned source revision and attribution.
Keep the Nuru service as the only Nuru/remote application writer; do not run a
second independently mutating plugin receiver. Obsidian and sync tools remain
external editors, so revision conflicts still require handling. Nuru's backend
must gain a narrow direct-capture operation rather than asking a model to save
text or spawning the RemoteNotes CLI for every request.

Preserve the existing public capture path `/v1/captures`, JSON `content` field and
201 response with `ok`, `uid`, `path` and revision. RemoteNotes uses an Inbox ULID
and UID frontmatter; retain that observable contract while defining how Nuru's
internal note identity, ownership, history and receipts map to it. Treat revisions
as opaque to clients. Add compatibility fixtures before migrating real shortcuts.

Keep the WP01 host on loopback (default 27125) for direct local use. Add a
separate remote-only listener on 127.0.0.1:27127 behind private Tailscale HTTPS
Serve; its accepting socket determines the remote credential boundary. Port
27126 remains available to the documented manual test servers.
Do not automatically overwrite an existing Serve route, enable Funnel, kill a
listener on 27124 or change installed shortcuts. Provide an explicit operator
migration procedure: inspect the route, stop the old receiver after accepted
writes finish, switch the route, verify a capture, then update clients as needed.
Retain a tested rollback procedure that preserves notes and request history.

## Service and client design

### Transport, identity and authorization

Serve the phone UI and versioned API from the same HTTPS origin. Reuse WP01 JSON
commands and resumable event streams; avoid a second chat implementation. All
conversation, event, request and note access checks run on the host for each
request. The accepting listener determines which credential class is allowed;
forwarding headers cannot grant access or turn a local request into a remote one.

Introduce revocable device credentials with explicit capabilities, at minimum:
`capture:create`, `chat:use`, and `notes:read`. A chat operation that writes also
requires the relevant server-side write capability and existing Nuru policy/
approval checks. A capture-only phone credential must never gain vault-read,
conversation or arbitrary-write access. Vault root is host configuration, not a
client parameter. Maintain one operator and vault, but isolate unauthenticated or
unauthorized devices from all resources; authenticate before reading request bodies.

Use a short-lived, single-use pairing code exchanged over HTTPS for the phone
browser's Secure, HttpOnly, SameSite session cookie. Apply Origin checks and CSRF
protection to browser mutations. Require explicit confirmation on the host when
a new device obtains chat/read permissions. Siri/HTTP Shortcuts use revocable,
capture-only bearer tokens. Existing capture tokens may migrate only with their
old limited capability, never as full chat credentials. Specify token/session
expiry, rotation, revocation and logout behavior; do not put long-lived secrets
in query strings, telemetry, browser localStorage or committed vault configuration.

Reuse the QR/onboarding patterns, not an assumption that current capture-only
pairing is sufficient for chat. If a temporary setup download contains a token,
it must be single-use or narrowly time-limited, no-store, private and revocable;
closing onboarding, rotation or service restart invalidates it. Do not log its URL.
Set request/body limits, upload deadlines, bounded queues and per-device quotas.
Keep model context/worker limits server-controlled.

### Captures, retries and receipts

New clients generate a durable request ID before submitting and reuse it for all
retries. Persist acceptance and a canonical payload hash, allocate the final note
identity/path once, and return the original receipt on an identical retry. A
changed body under the same ID is a 409 conflict. One hundred simultaneous copies
of one request must produce one note, not one note per connection.

Creation and receipt persistence cross a crash boundary. Extend the backend write
journal/receipt design explicitly: reserve the identity, record intent, use a
no-clobber create, and reconcile that exact identity plus expected content hash
after restart. If evidence cannot prove completion/noncompletion, expose uncertain
status and require reconciliation; never replay an uncertain mutation blindly.
Keep compact request tombstones; expiry cannot turn an old request into new work.
Document storage limits and reject new work rather than silently discarding the
records needed for retry safety. A duplicate request also consumes no model call.

Existing shortcuts that send only `content` can remain in an explicit legacy
single-shot compatibility mode, with no deduplication claim and no automatic retry.
Ship updated shortcut definitions that persist/send request IDs and query status
after an ambiguous timeout. The rollout is not complete until supported new
clients use that contract. Identical text with different IDs intentionally creates
separate notes. Client cancellation before acceptance sends nothing; after
acceptance, a disconnected caller can still have a committed capture.

Capture preserves the input body exactly, including Unicode, quotes and line
breaks, apart from the documented frontmatter envelope. It does not summarize,
classify, choose a path from supplied text, or invoke inference. Notes return an
actual backend revision and completion receipt. A receipt failure cannot be
reported as a successful save merely because the request reached the server.

### Browser chat and host lifecycle

Provide conversation list/resume, streaming Markdown, citations, pending-operation
status, Stop, server-timed questions and approved save actions. Reuse WP01 rendering
and protocol components where practical. Note links open a bounded authorized
preview; do not reveal host filesystem paths. Explicitly distinguish unsent drafts,
accepted work, running work, completed work and uncertain results.

Persist enough client request identity to recover from reload; avoid sending an
unsent draft automatically on reconnect. Resume an accepted turn by its ID and
stream cursor, never by reposting its text under a new ID. Sleeping/backgrounding
a phone can expire a ten-second approval: show the expired state and require a new
server-issued proposal; never silently extend or approve it. Device handoff of a
pending question must be explicit and preserve a single valid answer owner.

Package an opt-in macOS launch agent for the service. Validate configuration before
installation; document starting, stopping and uninstalling it. Do not enable login
items or reboot the user's Mac without authorization. Start without a model, so
capture/history remain available during an Ollama outage. The service must operate
with Obsidian closed. Model ownership and per-conversation isolation remain WP01's.

## Milestones and outputs

Each milestone is a separately reviewable commit or small PR. Record evidence in
`nuru/evals/obsidian_remote/wp02-remote-chat-capture.md`.

| Milestone | Outputs | Exit evidence |
| --- | --- | --- |
| M1 — Remote contract and migration | Threat/permission model, device scopes, retry and legacy contracts, schema mapping, frozen compatibility fixtures | Old capture payload/response fixtures pass; unauthorized capability matrix has explicit expected failures |
| M2 — Reliable direct capture | Backend capture operation, durable request/receipt reconciliation, status endpoint and quotas | Duplicate/race/crash matrix passes; captures succeed with every model provider disabled |
| M3 — Pairing and private exposure | Same-origin browser session, scoped shortcut credentials, revocation, Tailscale checks and migration/rollback guide | Real private HTTPS test; invalid/revoked credentials cause zero vault reads/writes or inference |
| M4 — Phone chat and shortcuts | Responsive web client, resumable streams, approvals, request-aware iPhone/Android capture templates | Automated browser/network fault tests; real phone Wi-Fi/cellular acceptance |
| M5 — macOS operation and release | Opt-in launch-agent packaging, restart/recovery diagnostics, install/uninstall docs and evidence archive | Model-free host lifecycle checks; authorized real login/reboot check or explicitly incomplete release gate |

## Complete test strategy

### Fixtures and automated layers

Reuse WP01's disposable vault, adding Inbox captures, duplicate text, Unicode
frontmatter-like bodies, oversized input, conflicting pre-existing target paths,
read-only folders, simulated disk-full/history failures and forged capture IDs.
Create separate credentials for capture-only, chat/read, expired and revoked
devices. Keep tokens and rubric answers outside the vault. Hash authored files and
check exact mutation sets after every case.

Run unit/contract tests for schemas, permission decisions, pairing state machines,
body preservation and receipts. Run real loopback HTTP tests with temp vaults and
fake providers. Run browser tests against the real service with a deterministic
network proxy able to cut a connection at selected protocol/write boundaries.
Use SQLite/filesystem fault seams and process termination barriers for crash tests.
Do not infer crash recovery from an exception-only mock.

| Scenario | Required result |
| --- | --- |
| 100 parallel retries of one capture ID | Exactly one note and one logical completion; identical receipts |
| Same text with 20 different IDs | Twenty distinct notes; no content-based deduplication |
| Changed payload under an existing ID | Conflict, zero additional note effects |
| Lost reply before/after commit; crash before/after receipt | Retry/status identifies the original operation; no duplicate capture; ambiguous state explicitly uncertain |
| Cross-device/foreign-vault/foreign-conversation access | Rejected before disclosure, model or mutation dispatch |
| Expired/rotated token, revoked browser session, replayed pairing code | Rejected; no resurrection of credentials on restart |
| CSRF, wrong Origin/Host, forged proxy headers, malicious Markdown | No unauthorized action or script execution; existing conversation remains usable |
| Oversized/stalled/invalid body; flood beyond quota | Bounded memory/time and explicit rejection; other clients/status remain responsive |
| Wi-Fi interruption, duplicate event delivery, phone reload/sleep | Resume original turn; no duplicated text or write; expired approvals remain expired |
| Model unavailable, Obsidian closed | Capture and authorized history/status still work; chat failure is clearly scoped |
| Host restart during accepted work | Recover ledger and receipts; no automatic replay of uncertain writes |
| Legacy shortcut and port/route migration | Documented old contract preserved; no hidden auto-retry; rollback leaves every note and receipt intact |

Simulated network profiles: 0, 50 and 150 ms added round-trip delay, plus a ten-second
outage at scripted points. Test both upload and response-stream interruption.
The proxy makes behavior reproducible; it does not replace actual phone/Tailscale
acceptance. Routine CI has no paid APIs, live models, personal vault access or
publicly exposed listener. Use pinned browser dependencies and existing Python
checks, plus plugin/browser build and contract tests introduced in WP01.

### Real-device acceptance

Record host/macOS, browser/OS, Tailscale and source versions. For iPhone Safari and
Android Chrome, perform on both Wi-Fi and cellular: pair, resume a conversation,
stream, open a cited note, approve/refuse a write, let a question expire, suspend
and reopen, submit a capture, lose its reply and recover the receipt, rotate/revoke
credentials, then verify rejection. Repeat capture through iPhone Shortcuts and
Android HTTP Shortcuts using multiline text with quotes and emoji. Compare saved
bytes with input; a success toast alone is insufficient. The Mac stays the same
host. If either physical device is unavailable, mark that platform unverified.

Quit Obsidian while keeping the service running and repeat a capture and scripted
chat. After explicit authorization, test launch-agent login/reboot recovery. If
reboot is deferred, do not claim automatic startup has passed. Validate uninstall
without deleting notes, history or device records without an explicit choice.

### Efficacy and performance

With models disabled, run 30 captures per network profile after three warmups.
Report acceptance-to-durable-receipt p50/p95, total device time, note-byte fidelity,
duplicate count, recovery delay, idle resources and rejected-request impact.
Targets: 100% content fidelity, zero unintended duplicates, p95 durable receipt
<=2 seconds at <=150 ms added RTT for payloads <=16 KiB, and reconnect-to-status
<=2 seconds after connectivity returns. Report large-payload results separately.

For scripted chat, compare the same WP01 turns locally and remotely in alternating
order, 30 turns/route. Report queue time and transport/render overhead separately
from model time. Target additional p95 first-render overhead <=500 ms at <=150 ms
RTT. Record observed Tailscale timings separately from simulated profiles.

Only with per-run approval, execute six fixed cases x five repetitions per route
(local and phone): read, search, follow-up, new note, shared-write refusal, and
reconnect after an accepted write. Use Qwen3.5 4B, Nomic v2 only for embedding, and
frozen source/model/config/fixture hashes. Grade answer accuracy, exact note effects,
unauthorized actions and time to correct result. Include every timeout/failure;
any unauthorized or duplicate write fails the release regardless of mean accuracy.
Capture never needs inference and must be tested with model access blocked.

## Completion gates, release and rollback

All deterministic critical cases and the supported physical-device matrix pass;
zero duplicate/unauthorized mutations and zero cross-device disclosure are allowed.
A successful legacy-only capture demo does not satisfy reliable-retry acceptance.
Both remote clients use the same conversations, approval rules and mutation
receipts as the Obsidian panel. Preserve current CLI/Qt behavior and all existing
note protections.

Deliver a phone web client, scoped pairing and revocation, direct capture/receipts,
updated shortcuts, migration/rollback guide, opt-in launch agent, protocol fixtures,
raw evidence and a results note. Publish source/model/runtime hashes, exact commands,
percentiles, failures and explicitly deferred checks. Never publish live tokens or
personal note content in evaluation artifacts. The prior failed live indexing
contention gate is not resolved merely by adding remote access; schedule that
work separately until new approved measurements demonstrate otherwise.
