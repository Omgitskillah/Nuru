# WP03 — Vault maintenance reports and review inside Nuru

Status: proposed, 2026-10-09. Requirements and thresholds are proposed; this document
does not claim a maintenance dashboard already exists or that model-assisted
maintenance has passed its quality gates.

Depends on [WP01 service and Obsidian panel](01-local-obsidian-chat.md). Local
implementation can begin after WP01; remote acceptance and device permissions use
[WP02 remote chat and capture](02-remote-chat-and-capture.md).

## Outcome and user workflow

Open a Vault health panel in Obsidian, see the latest completed scan, filter its
findings, open the exact evidence in a note, and acknowledge or suppress an item.
After manually editing a note, rescan and see whether the finding really resolves.
The same report is available in the phone web client for authorized devices.

The panel makes the deterministic stewardship work merged in PR11 usable without
reading JSON logs. It does not add an automatic repair engine, expand the default
model tool list, or turn a heuristic suggestion into a confirmed defect. macOS is
the host target; mobile clients are the WP02 browsers, not Obsidian mobile plugins.

## Existing capability and limits

The backend already has `Steward`, `Snapshot`, revision-bound findings, pagination,
feature-specific details and an optional SQLite `State` store. Review actions are
`acknowledged`, `suppressed` and `open`, and require an expected evidence revision.
Complete rescans can mark missing findings resolved. Existing CLI/MCP reports and
session log summaries remain supported.

There are ten report families plus semantic index maintenance. Their user-facing
labels must reflect the actual code and configured coverage:

| Area | Show now | Do not imply |
| --- | --- | --- |
| Properties | Violations of explicitly configured field/type schemas | Automatic discovery of a user's preferred schema |
| Links | Supported broken, ambiguous and unsupported references with locations | Perfect resolution of every Obsidian/plugin link syntax |
| Attachments | Missing embeds and available usage/inventory evidence | Safe deletion from absence of recognized references alone |
| Tasks | Explicit overdue tasks and contradictory mirrors | Completion inferred from prose or a single conflicting checkbox |
| Tables | Declared numeric schemas and computed-total mismatches | Arbitrary spreadsheet formula understanding |
| Project review | Configured review dates and missing explicit next actions | Old notes or paused projects being inherently stale |
| Inbox | Rules-based routing previews and abstentions | Autonomous moves or model classification |
| Duplicates | Exact-copy evidence and duplicate inventories | Semantic similarity search or automatic merging |
| Claims | Structured comparisons with supported units and conditions | General natural-language contradiction detection |
| Navigation graph | Parsed note/link relationships | Unstated project dependencies or generated factual connections |
| Semantic index | Availability, backlog, errors and indexed revisions | An always-current index or a passing live contention gate |

Some evidence is an inventory rather than a finding. Provide a bounded details
view so exact duplicate groups and graph information are visible without inventing
new defect counts. Identify disabled packages, missing schemas, unknown coverage,
parser diagnostics and inaccessible notes explicitly.

## UI and service design

### Report snapshots and scan lifecycle

The host owns one stewardship scheduler per vault, shared with the existing
background worker. UI clients subscribe; they do not start competing workers.
Support an explicit manual scan plus existing opt-in periodic checks. Read-only
inspection of the latest report must not silently enable a worker or start model
inference. Coalesce repeated scan requests and expose queued/running/completed/
failed/cancelled state. Retain the previous completed snapshot while another scan
runs, clearly marked with its age and settings.

Use versioned, durable scan IDs with immutable result snapshots, generation time,
source/configuration identity, explicit `as_of` date/timezone, completion state and
diagnostics. Existing offsets are not sufficient across changing scans: bind page
cursors and exports to a scan ID and stable sort order. Expire retained scans with
an explicit reload response. Never mix pages from two different vault revisions.

Keep review state separate from immutable scan evidence. A review action returns
an updated state/version overlay, without rewriting the historical evidence.
Before accepting it, validate finding ID, expected evidence revision, actor
permission and freshness against current relevant sources/configuration. The
existing State.review comparison against stored data alone is insufficient if a
note changed since the last scan: revalidate or rescan first. If evidence changed,
return a stale-evidence conflict and offer refreshed evidence, not a blind retry.
Serialize review and scan reconciliation so an older scan cannot overwrite a
newer review decision. Preserve suppression only under existing evidence rules.

Proposed additional surface, using WP01's authentication and request IDs:

| Operation | Behavior |
| --- | --- |
| `GET /v1/maintenance/status` | Enabled packages, worker state, latest scan, queue and bounded index status |
| `POST /v1/maintenance/scans` | Explicit model-free scan request, coalesced against an identical active request |
| `GET /v1/maintenance/scans/{id}` | Immutable summary, coverage, configuration identity and diagnostics |
| `GET /v1/maintenance/scans/{id}/findings` | Filtered findings with scan-bound pagination |
| `GET /v1/maintenance/scans/{id}/details/{package}` | Bounded inventory/evidence details; no unbounded graph or asset dump |
| `POST /v1/maintenance/findings/{id}/review` | Revision-checked acknowledge/suppress/reopen in the excluded state sidecar |
| `GET /v1/maintenance/scans/{id}/export` | Explicit user-requested JSON or Markdown export of that snapshot |

Add `maintenance:read` and `maintenance:review` device capabilities to WP02's
registry; scan execution can use a separately granted `maintenance:scan` capability.
Capture-only tokens receive none. Local plugin credentials need equivalent checks.
Do not send report excerpts, paths or counts to clients without report access.
Review permissions authorize sidecar state, not modifications to authored notes.

### Panel interaction

Provide a Vault health tab with last scan time, reference date, completion/coverage,
counts by package, diagnostics, Rescan and filters. Selecting a finding shows its
classification (confirmed defect, conflict, or needs review), source note/line,
quoted evidence, revision, rule and supported suggestion. Long bodies, findings and
graphs use pagination/virtualization. Provide keyboard navigation and accessible
status/focus announcements.

Open a cited location using Obsidian navigation. The remote browser uses an
authorized note preview. If a note moved or changed, resolve its current identity
where unambiguous and clearly indicate changed evidence; never jump silently to
an unrelated duplicate basename. Notes and quoted HTML remain untrusted rendering
input. Attachment inspection never auto-loads a remote URL or offers an automatic
Delete action based only on current parser coverage.

Buttons: Acknowledge, Suppress, Reopen, Open note, Copy evidence and Rescan. Do not
provide a manual Mark resolved button: resolution is a complete-scan conclusion,
not the same as hiding an item. Acknowledgement/suppression changes only excluded
state storage. If state storage is unavailable, show the report as read-only and
say review actions cannot be saved; never display a successful persistent state.
Incomplete scans must not resolve unseen findings or present zero findings as a
clean bill of health. Distinguish no findings, checks disabled and scan failed.

Exports are user-requested and tied to a snapshot. Default to a download/copy outside
the vault. Saving a report into the vault, or asking chat to propose a note edit,
uses the same explicit write/approval path as other Nuru actions. It must not
bypass shared-folder guards or resurface credentials in exported settings.

### Chat integration and background cost

Add an explicit Explain this finding action that attaches the selected bounded
report evidence to a chat turn. Show what is being attached and its age. Include
source revisions and coverage limits; treat note text as supporting data, not
instructions. The model can explain a result but cannot change its verified status,
computed totals or review state. No automatic repair follows an explanation.
This action loads the configured chat model only when invoked; the core panel
works with all inference blocked. Do not automatically append whole reports to
every conversation or expand MODEL_TOOLS as part of this package.

Keep property/link/table checks separate from embedding maintenance. Reading index
status is read-only; the panel does not start a rebuild or pull a model. Display
existing index errors/backlog with recovery guidance. Any future reindex action
requires its own operational contract and test run authorization. The historical
live embedding-contention and advisory-quality gates failed; this UI cannot claim
them fixed. Where a deterministic scan itself affects foreground responsiveness,
yield/coalesce background work and measure the effect before enabling it broadly.

## Milestones and outputs

Each milestone is a separately reviewable commit or small PR. Append evidence to
`nuru/evals/obsidian_remote/wp03-maintenance-ui.md`.

| Milestone | Outputs | Exit evidence |
| --- | --- | --- |
| M1 — Findings/coverage contract | Typed presentation mapping for all eleven areas, stable scan IDs/cursors, state overlay and fixtures | Existing backend reports map without changed grades; disabled/incomplete/error states are distinct |
| M2 — Host report API | Shared scheduler, snapshot retention, pagination/details/export, revision-checked review endpoints and device scopes | Scan/review races, permissions, restart and stale-evidence tests pass |
| M3 — Obsidian panel | Summary, filters, evidence navigation, accessible review controls and manual rescan | Real macOS Obsidian workflow; authored bytes unchanged except deliberate manual fixture edits |
| M4 — Phone view and chat handoff | WP02 browser report view, authorized previews and bounded Explain this finding | No report access for capture-only devices; explicit evidence handoff and offline/stale state tested |
| M5 — Efficacy and release | Seeded-case comparison, scale/foreground benchmarks, operator pilot, screenshots and usage documentation | Critical gates pass; unverified model/pilot/platform claims remain explicitly deferred |

## Complete test strategy

### Test vaults and evaluator-only truth

Use the tracked `nuru/evals/vault_steward/validation_fixture.py` as the baseline for
a freshly generated `NuruMaintenanceUITestVault`; do not reuse a manually edited
copy. Its fixed-date configuration produces 16 expected finding occurrences plus
feature inventories. Keep oracles outside the model-visible vault. Freeze the
reference date to 2026-10-09 and timezone to Europe/Berlin for baseline comparisons.

Add UI-specific fixtures for changing evidence between scan and click, renamed
notes with duplicate basenames, deleted notes, malformed frontmatter, failed or
excluded sources, stale index revisions, unsupported plugin references, arbitrary
HTML in evidence and 10,000 paginated synthetic findings. Include deliberate valid
links, paused/completed projects, equivalent units and differing claim conditions
as negative controls. Seed separately identifiable duplicate inventories; do not
count them as defect findings if the backend does not.

Hash every authored file before/after each automated scenario. Expected writes
are limited to the named state/host databases and explicit manual-edit fixtures.
Scans, views, navigation, filters and review buttons must not mutate notes.

### Automated correctness and integration

| Area | Required cases and assertions |
| --- | --- |
| Report mapping | Every backend classification and inventory renders with its actual severity/coverage; baseline 16/16 findings and all negative controls match CLI |
| Paging/filtering | No missing/duplicate IDs across pages; filters/counts agree; concurrent new scan cannot change the active snapshot's pages; expired cursor requires reload |
| Evidence navigation | Correct file and line for Unicode/aliases/headings; changed, missing or ambiguous source displays explicit state; no host-path/traversal escape |
| Review state | Acknowledge/suppress/reopen survives restart when valid; same evidence retains status; changed evidence reopens or rejects stale actions; no false success on locked/unrelated state DB |
| Races | Note edit before review, edit during scan, review versus reconcile, two clients reviewing one item, rename/delete while a page is open; deterministic conflict/version outcomes |
| Incomplete scans | Parse/read/permission errors, cancellation, worker crash, missing package config; keep prior report and unresolved findings; never declare incomplete evidence clean/resolved |
| Permissions | Capture-only, revoked and wrong-vault credentials cannot get reports, counts, exports or review state; read-only devices cannot review or scan |
| Rendering/exports | Hostile HTML/links, long quotes, large graphs and formulas; bounded/safe display; no auto-fetch; export exact requested snapshot without credentials |
| Chat handoff | Only selected evidence within a frozen token/byte cap; source revision retained; stale evidence labeled; hostile note instructions do not become control instructions |
| Resource ownership | Opening many panels creates one worker; disconnect/reconnect does not rescan repeatedly or leak subscriptions; a model outage leaves deterministic reports available |
| Index visibility | Existing/missing/incompatible/locked index states display accurately; viewing status causes zero embedding requests and no implicit rebuild |

Run Python backend/service tests, protocol fixtures, JavaScript/component and
browser tests, and the full existing suite. Use fake chat providers and block
network/model calls in deterministic tests; fail the test if any unexpected
provider invocation occurs. Exercise real SQLite concurrency and filesystem fault
seams; do not rely only on mocked return values. CI installs/builds the plugin,
but real Obsidian acceptance remains a separate macOS lane.

### Real application and human validation

In a fresh macOS Obsidian test vault, open the panel, verify the 16 baseline
findings and inventories, filter to tables, open `Datasets/costs.md`, and manually
correct EUR 20.00 to EUR 17.50. Rescan and verify exactly that table finding resolves.
Repeat for one broken link. Suppress a remaining finding, restart the service and
plugin, then change its evidence and verify the old suppression does not conceal
the new evidence. Create a parse/read error and verify incomplete status. Confirm
state actions did not rewrite note bytes. Repeat the report/review subset on both
WP02 phone browsers with correctly scoped credentials.

Run a within-person pilot with at least three Obsidian users and ten matched cases
per participant. Compare current CLI reports against the panel; counterbalance
order and use equivalent, disjoint fixture variants to reduce learning effects.
Measure time to locate correct evidence, choose the right action, and verify a
manual correction; record incorrect suppressions, mistaken resolutions, abandoned
cases and perceived clarity. Proposed target: at least 25% lower median time to
correct action, no increase in error rate, and zero mistaken destructive actions.
Publish participant count, per-case results and uncertainty; a small pilot is not
proof of broad user benefit. If recruitment is unavailable, mark efficacy pending
and release only as a tested technical preview, not a validated productivity gain.

### Scale, latency and foreground impact

Generate 1,000- and 10,000-note vaults with controlled defect density and separate
10,000-finding UI data. Record fixture hashes. After three warmups, run 30 cycles
per size for cached summary/first page, filter, evidence open, review and rescan.
Proposed targets: cached first usable page p95 <=500 ms locally, filter response
<=100 ms for already loaded data, and zero unbounded rendering of large inventories.
Record cold scan wall time, files/bytes read, database writes, CPU, RSS and cancellation
latency rather than declaring a hardware-independent full-scan deadline.

Compare scan off/on in alternating order for 30 matched scripted foreground turns
per fixture size. Report acceptance, first-render and completion p50/p95 and any
missed events. Target foreground p95 slowdown <=10%; if it fails, keep periodic
scans off by default during active chat and fix scheduling before broader rollout.
Do not mix this deterministic scan result with the earlier Nomic contention result.

An optional approved Qwen3.5 4B lane evaluates Explain this finding on ten frozen
cases x five repetitions, including contradictory task mirrors, incorrect totals,
unknown attachment usage and differently conditioned claims. Grade source fidelity,
correct explanation, unsupported assertions, output tokens and time. Compare against
plain evidence cards for the same cases. Require zero invented completion/deletion/
sharing permissions; otherwise keep explanations experimental and label them as
model output. Nomic v2 is unnecessary for this lane unless a separately approved
retrieval variant is being tested. Preserve failures in the saved evidence.

## Completion gates and release outputs

The panel must reproduce backend evidence and grade without unauthorized note
changes, false resolutions, stale-review acceptance or permission leaks. All
critical deterministic tests and actual macOS acceptance pass. Authorized users
can inspect the same report locally and remotely; review status survives restart
and invalidates correctly when evidence changes. Observability distinguishes
incomplete, stale and unavailable states from a healthy scan.

Deliver the panel/web views, API schemas, scheduler integration, scan storage,
revision-aware review actions, fixtures/oracles, tests, install/setup guidance,
manual walkthrough, screenshots, performance records and results note. Store
sanitized raw artifacts and hashes under `nuru/evals/obsidian_remote/`; archive
large records with deterministic gzip and retain readable summaries. Record source,
config, model (if any), OS/app versions and exact commands. Separate technical
release readiness, optional model-quality acceptance and human efficacy; none
substitutes for the others. A later repair engine requires its own work package.
