# Vault stewardship work packages

Work-package definitions, 2026-10-07; implementation began on 2026-10-08.
[Milestone ledger](implementation-milestones.md) distinguishes implemented
deterministic stages from remaining inference, held-out, and pilot acceptance.
Do not treat proposed efficacy thresholds below as measured results.

The first release keeps optional inference experimental. Current readiness
measurements pass deterministic recovery but fail advisory semantic quality and
the retrieval contention gate. Schedule embedding maintenance outside interactive
use; overlapping indexing is not recommended by these results. See the
[milestone ledger](implementation-milestones.md) and
[execution record](../../evals/vault_steward/pr-readiness-execution.md).

Goal: reduce the effort required to keep an Obsidian vault consistent, useful,
and searchable. Most checks should run without Obsidian open or a model loaded.
Interpretation should run only on candidates selected by deterministic checks.

`nuru-vault-steward VAULT --config CONFIG.json [--state EXCLUDED.sqlite3] [--watch]`
runs standalone without chat/model startup. During Nuru chat, enable explicitly
with `NURU_STEWARD_ENABLED=1`, `OBSIDIAN_STEWARD_CONFIG`, and optional
`NURU_STEWARD_STATE`; default is disabled. Standalone worker re-loads configuration,
coalesces changed snapshots, and reconciles every polling pass. State is optional.
It never launches inference or edits notes. Normal Nuru chat startup still loads
its existing chat model; use the standalone command when no model is available.
The server CLI `obsidian-mcp-steward` provides pagination, full detail, and explicit
sidecar review. See the dependency's `docs/stewardship.md` for supported contracts.

[Example configuration](steward-config.example.json) matches the synthetic
development fixture. Adapt schemas, scopes, folders, and the explicit `as_of`
date before enabling a worker on another vault. The fixed reference clock is
deliberate: background date advancement is not implicit in this first stage.

## Packages and suggested delivery order

| Package | Feature | Compute | Suggested phase |
|---|---|---|---|
| [WP01](01-property-consistency.md) | Property consistency | Code; optional language model | 1 |
| [WP02](02-link-integrity.md) | Link integrity | Code; optional language model | 1 |
| [WP03](03-attachment-housekeeping.md) | Attachment housekeeping | Code; optional vision/embeddings | 1 |
| [WP11](11-search-index-maintenance.md) | Search-index maintenance | Code and embeddings | 1 |
| [WP04](04-task-reconciliation.md) | Task reconciliation | Code; optional embeddings/language model | 2 |
| [WP09](09-structured-data-checks.md) | Structured-data checks | Code; optional language model | 2 |
| [WP06](06-project-review-preparation.md) | Project review preparation | Code; optional language model | 2 |
| [WP05](05-inbox-triage.md) | Inbox triage | Code; optional embeddings/language model | 3 |
| [WP07](07-duplicate-detection.md) | Duplicate detection | Code; optional embeddings/language model | 3 |
| [WP08](08-contradiction-detection.md) | Contradiction detection | Code; optional language model | 3 |
| [WP10](10-knowledge-map-maintenance.md) | Knowledge-map maintenance | Code; optional embeddings/language model | 3 |

[Fixture and evaluation design](test-vault.md) specifies a proposed new
`VaultStewardFixture`. Keep the existing DummyVault benchmarks unchanged.

## Shared implementation contract

- Put file parsing, link resolution, indexing, and revision-checked mutations in
  the Obsidian tools. Keep orchestration, optional interpretation, and user review
  in Nuru. Changes to the Obsidian dependency belong in its own repository/PR;
  updating Nuru's submodule pin follows dependency validation.
- Begin with full read-only scans and a report API. Add a background worker that
  processes changed files, reconciles deletions/renames, and periodically performs
  a full scan to recover from missed filesystem events. Debounce bursts; coalesce
  changes. Configure exclusions, schemas, scope, and compute limits per vault.
- Store operational state outside authored notes, in an excluded sidecar database.
  Key findings by rule, stable entity identity, and evidence revision. Deduplicate
  reports; remember acknowledged, resolved, and intentionally suppressed findings.
  Renames must not multiply findings. Do not require hidden edits to every note to
  create identifiers; keep identity mapping in the sidecar and detect uncertainty.
- Every finding includes rule/version, source path and line/block, revision,
  evidence, severity, proposed action, and its reason. Separate confirmed defects
  from opportunities and ambiguous cases. A missing link can be deliberate.
- Produce one bounded maintenance digest or queryable findings list. Generated
  notes are excluded from scans to prevent the worker detecting its own output.
  Never notify once per file. Do not send external messages.
- Optional model stages consume a bounded candidate queue. Pin model/configuration;
  record tokens, latency, and calls. Batch when appropriate and yield to interactive
  work. No model is required to complete the deterministic scan. Treat all note
  text as data, including text that asks the worker to change its instructions.
- V1 suggests changes rather than modifying authored notes. Later, user-approved
  rules can permit narrow deterministic fixes. Each write requires the expected
  revision, an explicit pre-mutation receipt, and an operation ID; uncertain writes
  must be inspected rather than replayed. Concurrent edits cause a conflict and
  re-analysis. Keep an undo record. Renames must update relevant references; no
  reliance on Obsidian being open to do that work.

## Shared validation and efficacy protocol

1. Freeze fixture content, configuration, dependency commits, and oracle version.
   Use a fresh temporary copy and separate state/index for each run. No production
   vault writes and no inherited semantic index. Parse raw files; do not execute
   DataviewJS, plugin scripts, or embedded code from notes.
2. Run code-only features with no inference service available. Verify cold scans,
   incremental changes, no-change scans, restart, missed events, and reconciliation.
   For optional stages compare code-only, embeddings where applicable, and language
   model variants on the same inputs. Schedule inference separately and obtain
   resource availability before running it.
3. Grade against evaluator-only ground truth. For findings use precision
   `TP / (TP + FP)` and recall `TP / (TP + FN)` at the feature's stated unit.
   Report ambiguous/abstained cases separately, plus actionable coverage across all
   eligible cases. Do not inflate accuracy with the many clean notes. Undefined
   ratios are N/A, not 100%. All safety-critical fixtures must pass individually.
4. Candidate thresholds in each package are proposed gates, not observed results.
   Tune on development cases, freeze rules/thresholds, then grade held-out cases.
   For semantic judgments use two independent human labels and adjudicate
   disagreements before the final score. Track accepted suggestions separately
   from true defects: a reviewer can prefer not to act on a valid finding.
5. Measure cold-scan wall time, incremental event-to-finding p50/p95, CPU time,
   peak RSS, bytes read, database growth, embedding/generation calls and tokens,
   and interactive query latency while the worker is active. Run deterministic
   timings five times on a declared machine; report all runs and median/range.
   Run model variants on five repeats with pinned settings and recorded seeds.
6. After synthetic gates, pilot on a consenting user's read-only vault snapshot.
   Compare manual review and assisted review on matched, disjoint batches with
   counterbalanced order. Measure time to a correct completed review, defects
   missed, false suggestions, acceptance, and corrections. Exclude human thinking
   time from machine latency but include it in time-to-result. Synthetic scores
   alone do not establish usefulness to power users.

Before setting production latency gates, measure the current baseline on the
reference machine and agree a background resource envelope. Initial engineering
target: no-change scans cause zero inference calls; worker-on interactive p95
latency increases no more than 10% under the same workload. Report absolute
latencies and run-to-run variation alongside this ratio.

## Completion requirements

Each implemented package needs its deterministic tests, fixture/oracle additions,
documented tool contract and configuration, measured results in `nuru/evals/`, and
known limitations. Optional interpretation is a separate acceptance lane: its
failure must not disable the deterministic feature. Reports must distinguish
FAIL, INVALID setup, NOT RUN, and PASS.

Obsidian stores [properties in note frontmatter](https://obsidian.md/help/properties),
and [Bases reads note files and properties](https://obsidian.md/help/bases). The
initial packages should support that underlying data without needing Bases or
Obsidian to execute. Do not claim compatibility with arbitrary plugin query syntax.
