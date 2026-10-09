# WP01 — Local Obsidian chat backed by a persistent Nuru service

Status: proposed, 2026-10-09. These are implementation requirements and proposed
acceptance gates, not capabilities already shipped or measured results.

Sequence: this package establishes the service and protocol used by
[WP02 remote chat and capture](02-remote-chat-and-capture.md) and
[WP03 vault maintenance UI](03-vault-maintenance-ui.md).

## Outcome and user workflow

On macOS, open an Obsidian sidebar, send a message, watch Nuru answer, follow a
note citation, answer a write-approval question, and continue the conversation
after closing and reopening the panel. Closing Obsidian leaves the Nuru service
available for future remote clients. The existing CLI and Qt UI continue to work.

The first release supports one configured vault and one operator, with multiple
conversations and clients. Multiple independently configured vaults, hosted public
accounts, Windows, Android/iOS Obsidian plugins, and Signal are outside this package.
Remote phone browsers belong to WP02. No model-assisted maintenance or new model
tool is introduced here.

## Starting points and ownership

- `nuru/src/core/session.py` already separates conversation execution from Qt and
  yields text, tool, question, failure and other events. Approval callbacks also
  occur mid-turn; forwarding only yielded events would miss those questions.
- `conversation_store.py` persists messages and write attempts. It is not yet a
  network request ledger or resumable event log. Preserve its uncertain-write rule.
- `questions.py` defines structured questions and a current ten-second mid-turn
  deadline. Preserve that behavior initially; enforce deadlines on the host.
- RemoteNotes' plugin is on `feat/obsidian-background-capture`, not its main branch:
  [source](https://github.com/Omgitskillah/RemoteNotes/tree/feat/obsidian-background-capture/obsidian-plugin).
  Reuse suitable settings, installer and lifecycle patterns with attribution and
  a recorded source commit. Its desktop HTTP receiver is not a chat backend.
- Put the new plugin under `plugins/obsidian/` in Nuru. Implement the host under
  `nuru/src/core/service/`, exposed as `uv run nuru-serve`. These paths and command
  are proposed outputs, not existing entry points. Keep note mutations in the
  existing Obsidian MCP backend; do not copy RemoteNotes' independent note writer.

## Architecture and contract

Use authenticated JSON commands and an HTTP event stream (SSE framing). The
Obsidian client can consume the stream with authenticated fetch; verify support
in the actual macOS Obsidian runtime before building the full UI. The same event
schema will support a same-origin phone browser in WP02.

Proposed versioned surface:

| Operation | Contract |
| --- | --- |
| `GET /v1/status` | Authenticated service/version/vault identity and readiness; no secret or arbitrary host-path disclosure |
| `POST /v1/conversations` | Create a conversation bound to the configured vault and authenticated principal |
| `GET /v1/conversations` and `GET /v1/conversations/{id}` | Authorized summaries and history, bounded pagination |
| `POST /v1/conversations/{id}/turns` | Required client request ID; return accepted turn ID before generation completes |
| `GET /v1/turns/{id}/events?after={sequence}` | Replay then subscribe to ordered events; cursor is non-secret |
| `POST /v1/turns/{id}/cancel` | Idempotent cancellation request; do not imply rollback of a completed tool |
| `POST /v1/questions/{id}/answers` | Answer tied to principal, conversation, turn, deadline and the proposed operation |
| `GET /v1/requests/{id}` | Return the persisted request state and result/receipt after reconnect |

Each event has protocol version, conversation ID, turn ID, monotonic sequence,
type and bounded payload. Define accepted, queued, text, tool-start/tool-result,
question, write-receipt, failed, cancelled and completed events. Terminal states
are mutually exclusive. Model reasoning is optional display data, never required
for completing a turn. Exclude credentials and unnecessary note contents from logs.

Persist acceptance before dispatch. Scope request IDs to the authenticated
principal, vault and operation; bind them to a canonical payload hash. An identical
retry returns the existing turn; the same ID with different content is a conflict.
Do not promise exactly-once arbitrary side effects: a crash after a tool write but
before its receipt remains uncertain and must not automatically replay the write.
Persist enough event/terminal state to reconstruct the displayed answer after
reconnect. Set retention and cursor-expiry behavior explicitly; an expired cursor
returns a resync response, never a new turn. Keep compact request tombstones for
this release so old IDs cannot silently become new work.

A bounded worker queue owns synchronous Session and SQLite objects in their
creation thread. HTTP handlers must remain responsive while a model call or
approval wait is blocked. Serialize turns per conversation and initially allow
one active generation across this service, with a visible bounded queue. Reject
excess work clearly. Acquire this slot before loading another model. This does
not schedule unrelated external Ollama clients.

Separate service resources from conversation resources: one maintenance/index
worker per configured vault, shared model ownership, resumable conversation
construction, and explicit service shutdown. Closing a tab/conversation must not
unload another client's model or stop an Ollama process owned by somebody else.
Start the host without loading a model; load lazily for chat. Model availability
must not affect status/history or later model-free capture.

Bind to loopback on a documented configurable port, initially 27125 to avoid
RemoteNotes' 27124. Require a narrowly scoped local credential even on loopback.
Verify Origin/Host and reject unapproved browser origins. Store service secrets
outside the vault with restricted permissions. Choose and test an OS/app-local
credential store for the plugin; do not silently put credentials in synced plugin
data or URLs. The local pairing/configuration flow must identify the vault, and
reject accidental use of an Obsidian vault different from the host's vault.

## Plugin behavior

Provide a sidebar, command-palette entry and settings for connection status,
explicit connection/pairing, and vault identity. Render streamed Markdown, bounded
history, pending/failed state, Stop, tool progress and structured approval cards.
Display actual write outcomes rather than assuming a tool request succeeded.
Approved questions expire server-side; closing/disconnecting cannot mean Yes.
Answers from a different conversation or for an earlier operation are rejected.

Open note citations through Obsidian's vault-relative navigation API. Explicitly
attach a current note or selection only when the user requests it; do not silently
append the whole vault or active editor. Render Markdown without executing HTML,
script, event attributes, or unsafe links, including content found in notes.

On unload, release UI subscriptions/listeners. An accepted turn may continue on
the service and be resumed; cancelling requires an explicit Stop. Existing CLI/Qt
behavior stays covered. Moving them to the service is a later optional migration,
not a prerequisite for this first sidebar.

## Milestones and outputs

Each milestone is a separately reviewable commit or small PR, with its evidence
appended to `nuru/evals/obsidian_remote/wp01-local-chat.md`.

| Milestone | Outputs | Exit evidence |
| --- | --- | --- |
| M1 — Freeze protocol and fixtures | Versioned request/event schemas, auth/ownership rules, error and cancellation states, dependency pins; tiny real-Obsidian streaming spike | Fixture protocol validates; a fake delayed reply streams in real Obsidian; no model required |
| M2 — Service lifecycle and persistence | `nuru-serve`, single-vault host, worker queue, resumable Session factory, request/event storage and migrations | Restart/reconnect and concurrent-client tests pass; CLI/Qt regressions pass |
| M3 — Chat and approvals | Sidebar, conversation picker, Markdown/citations, progress, Stop and approval cards | Scripted end-to-end turns exercise every terminal state and approval result |
| M4 — Package and install | Reproducible plugin bundle/manifest/styles, safe installer, local pairing and uninstall instructions | Install/upgrade/unload in a disposable Obsidian vault; settings/notes preserved |
| M5 — Acceptance and handoff | Benchmark results, screenshots, known limits, protocol compatibility matrix, WP02 handoff | All release gates below satisfied; live-model lane explicitly run or deferred |

## Complete test strategy

### Fixtures and isolation

Generate a disposable `NuruPluginTestVault` from tracked fixture definitions, not
from a personal vault. Include `nuru/`, `shared/`, `user/`, Unicode names, duplicate
basenames, Markdown with hostile HTML, a large note, and stale-revision edits.
The service selects its root; remote arguments cannot select another root. Keep
oracle answers outside the vault and outside model prompts. Give each run fresh
conversation/request databases and tokens. Hash authored files before/after;
assert an exact allowed change set for each mutation scenario.

### Deterministic automated lanes

| Layer | Required cases and assertions |
| --- | --- |
| Protocol and auth | Wrong/missing/revoked token, unknown version, oversized/malformed input, foreign Origin/Host, foreign conversation/event IDs, traversal and symlink paths; rejected calls make zero tools/model calls |
| Session equivalence | Same scripted model/tool sequence through direct Session and HTTP yields identical final answer, ordered tool calls and persisted note effects; timing/chunk boundaries may differ |
| Question routing | Yes, No, no answer, ten-second timeout, late/duplicate/wrong-client answer, reconnect while awaiting approval, Stop while waiting; zero writes without valid required approval |
| Delivery | Duplicate submit before/after completion, changed payload under same ID, two simultaneous retries, replay from every event boundary; one accepted turn and no duplicate displayed content |
| Failure/restart | Crash before acceptance, after acceptance, during generation, before write, after write before receipt, after completion before delivery; uncertain writes remain blocked and are never blindly retried |
| Scheduling | Two clients on one conversation and different conversations; ordering, bounded queue, overflow response, one active generation, responsive status/cancel; no cross-conversation history leakage |
| Lifecycle | Plugin reload, service restart, model unavailable, MCP disconnect, repeated open/close, shutdown during a turn; no orphan process/listener and no stopping externally owned Ollama |
| Rendering | Streaming split at Unicode/Markdown boundaries; hostile HTML, links and tool text; keyboard access, focus, scroll, long histories and code blocks |

Use scripted providers and temporary files for routine CI. Faults use explicit
barriers/hooks and controlled clocks, not timing-sensitive sleeps. Run the existing
Python suite, new service contract tests, JavaScript unit tests, browser component
tests and plugin build checks. A DOM mock does not establish real Obsidian support.
Pin the actual CI job commands/dependencies when M1 introduces those test runners.

### macOS application acceptance

Record macOS, Obsidian, Python, plugin and service versions. In a real disposable
Obsidian vault: install; pair; stream a long answer; open a citation; approve and
refuse a shared write; let approval expire; stop generation; reload the plugin;
resume history; quit Obsidian and verify the host still answers status; reconnect.
Verify authored bytes and receipts, not just UI toasts. No Windows test requirement.

### Performance and optional model lane

Measure direct Session versus HTTP/plugin with identical scripted inputs, after
three warmups, for 30 turns per route in alternating order. Separate service
startup, model load, queue time, acceptance, first event, first rendered text,
completion, and cancellation acknowledgement. Proposed gates: loopback p95 accepted
response <=250 ms, service-to-render event overhead <=100 ms, and Stop acknowledgement
<=250 ms. These do not bound model completion or rollback already-running tools.
After 20 open/close cycles require zero leaked subscriptions/workers and post-GC
RSS within 10% of the initial warm state; capture a heap profile if it exceeds this.

Only with approval for that run, compare current Qt/direct behavior with the
plugin on six frozen cases x five repetitions per route: read, search-and-answer,
arithmetic, authorized write, refused shared write and a follow-up conversation.
Use Qwen3.5 4B only; Nomic v2 only if search needs embeddings. Freeze model digests,
context, sampling and starting vault bytes. Alternate route order. Report rubric
accuracy, exact note diffs, time to correct result and failures separately; any
unauthorized write fails acceptance. No unapproved model calls in ordinary CI.

## Completion gates and handoff

All deterministic critical cases pass, existing CLI/Qt tests remain green, and
real macOS acceptance is recorded. No duplicate mutation, approval bypass,
cross-conversation leak or automatic replay of uncertain writes is acceptable.
Publish command lines, source revisions, seed manifest, machine versions, raw
results with hashes, latency percentiles and failures under `nuru/evals/obsidian_remote/`.
Large raw records may be deterministic gzip; redact credentials and note contents
not needed for grading. Mark proposed, automated-tested, manually-tested and
live-model-tested claims separately.

Deliver the service, plugin, schemas, installer, fixtures, tests, setup guide,
results note and known limits. WP02 may reuse only the versioned contract and
proven lifecycle; local success alone is not remote authorization validation.
