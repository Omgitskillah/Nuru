# Stewardship first milestones — 2026-10-08

Branch: `feat/vault-link-integrity`, based on the rebased work-package branch and
main. Each deterministic backend milestone has a separate Obsidian commit and
Nuru pin commit; shared integration, interpretation boundary, and regression
hardening also have their own commits. This implements first stages, not all
human/model efficacy acceptance gates in the original proposals.

## Implemented lanes

| Package | First supported stage | Remaining acceptance/scope |
|---|---|---|
| WP01 | Explicit property schemas, duplicate/syntax/type/date/value/cross-field checks | Vault-wide `.obsidian` property-type import; held-out schemas/inference efficacy |
| WP02 | Existing link resolver plus shared findings lifecycle | Model ranking and real-vault coverage |
| WP03 | Streaming asset hashes, exact duplicates, declared property/Canvas/body ownership | Trusted persistent hash cache; plugin coverage; visual similarity |
| WP04 | Fixed-clock ledger, statuses/due dates, explicit scoped mirrors | Plugin recurrence expansion and semantic task identity/completion |
| WP05 | Explicit metadata route previews and scope checks | Semantic destinations, new-topic/split suggestions, human triage efficacy |
| WP06 | Explicit review checklist, bounded task/decision evidence | Prose decision extraction, recent-change history, summary quality |
| WP07 | Exact byte groups, separate normalized groups, bounded lexical pairs | Embedding retrieval and semantic duplicate/group quality |
| WP08 | Structured numerical claims, declared units/scopes/dates/conditions | Broad prose extraction and contextual contradiction classification |
| WP09 | Declared pipe tables, Decimal totals/formulas/tolerance | Further table grammars/locales and model extraction |
| WP10 | Resolved graph and explicit metadata navigation | Useful semantic connections and navigation pilot |
| WP11 | Existing index identity, persisted queue/freshness, batching/yield and full hashes | Live digest/retrieval evaluation, lock/corruption stress and interactive contention |

Shared runner: explicit packages/configuration, bounded finding pages, optional
excluded state DB, acknowledgement/suppression scoped to evidence, restart and
conservative rename identity, reconciliation, standalone CLI, read-only MCP tool,
and default-disabled Nuru worker. No model tool allowlist change. No authored
file edits or production vault scans were made.

Optional interpretation is an injectable boundary tested with fake providers.
It requires per-run approval, rendered-chat token counting, budgets, source
revisions and verbatim citations. Live adapters/model benchmarks remain NOT RUN;
valid citations do not establish a correct suggestion. No automatic model stage,
repair, move, merge, delete, notification, or paid job is enabled.

Subsequent evaluation: the user authorized Qwen 3.5 4B-only local tests on
2026-10-09. See [the separate protocol and findings](qwen-steward-2026-10-09.md).
The earlier NOT RUN statements describe the 2026-10-08 milestone; the later
synthetic advisory evaluation does not complete the outstanding held-out,
embedding-index, or production efficacy gates.

## Independent fixture and grading

[Fixture/oracle](steward_fixture.json) contains 11 notes and 2 placeholder assets.
It is materialized into a fresh temporary vault; its oracle/configuration stay
outside that vault. The placeholders test bytes/references, not image decoding.
[Evaluator](steward_eval.py) compares namespace/source/rule/line occurrence
multisets and checks independently declared groups, storage totals, graph edges,
route, and numerical total. It verifies authored bytes before/after.

Results: **11/11 findings**, TP 11, FP 0, FN 0, precision/recall 1.0 for these
seeded review/defect occurrences. Exact-copy groups, asset groups/duplicate bytes,
graph edge, route, numerical total, scan completeness, unchanged authored bytes,
and zero model calls all pass. Clean note counts are not added to accuracy.
This is a development fixture, not held-out or production efficacy evidence.

Backend tests additionally cover malformed/duplicate metadata, schema changes,
symlinks, hidden exclusions, Unicode/BOM, large asset hashing, task history and
projects, malformed table rows and high-precision inputs, scope boundaries,
queue restart/cancellation/fake embedding failure, preserved-mtime edits,
full-rebuild state equivalence, stale review/interpretation, and fake budget guards.

## Timing protocol

[Raw results](steward-results-2026-10-08.json.gz) record all five repeats, commits,
source hashes, fixture hash, platform/Python, and peak process RSS. Reference
machine: Apple M4, 16 GiB RAM. The scale lane runs properties, links, tasks,
duplicates, and graph; it does not time all feature-specific schemas/assets or
models. Every sample is `Steward.run_once` wall time including reconciliation,
hashing, feature computation, and snapshot generation. Fixture creation and JSON
stdout serialization are excluded. Cold means a fresh parser cache, not flushed
OS storage caches. Background OS work was not fully isolated.

Median wall times in milliseconds (five repeats each):

| Notes | Cold | No change | One-note edit |
|---|---:|---:|---:|
| 1,000 | 1,312.314 | 646.298 | 668.307 |
| 10,000 | 13,056.784 | 5,848.962 | 5,775.746 |

Peak process RSS: 146,374,656 bytes (139.6 MiB). Every one-note edit
reparses exactly one snapshot note and one link-parser note. Other features may
recompute their metadata/candidates/graph; incremental parsing does not imply an
O(1) complete pass. Full hashing remains intentionally conservative. Asset hashes
are streamed but currently reread on no-change scans. Peak RSS is a process
high-water mark, not worker-only allocation.

## Reproduction and CI

Final isolated combined suite: **1,382 passed, 1 skipped in 16.85 seconds**. The
skip is the existing live semantic test requiring Ollama; no inference was run.
An initial test run inherited a developer index path and was INVALID setup; it
was rerun with explicit isolated settings shown below. Both standalone module
entrypoints returned the 11 fixture findings without changing authored bytes;
workflow YAML, lockfile, whitespace, and benchmark source hashes were checked.

```bash
uv run --locked python nuru/evals/vault_steward/steward_eval.py --sizes 1000,10000 --repeats 5 --output /tmp/steward-results.json
VAULT_PATH=/tmp/ci-vault OBSIDIAN_SEMANTIC_DB='' NURU_STEWARD_ENABLED=0 uv run --locked pytest -q nuru/tests servers/obsidian/tests servers/obsidian/vendor/access-logging/tests servers/deep-think/tests servers/laya/tests
```

Existing GitHub CI now explicitly includes the pinned Obsidian tests and isolates
index/worker settings. A manual `workflow_dispatch` option runs the 1K/10K × 5
scale evaluation and keeps the JSON artifact seven days; no automatic scale or
inference job is added. Manual dispatch becomes available when the default
branch contains that workflow definition. GitHub CI execution for these new
milestones remains NOT RUN until publication/approval.

## Remaining evidence

Live embeddings/chat, model-quality/held-out labels, real-vault review pilots,
worker-on interactive latency, filesystem event-to-finding p95, CPU-time/I/O stress,
and measured human efficacy are **NOT RUN**. Every future inference run requires
explicit user permission; real vaults and paid resources require prior approval.
Incompatible indexes require a deliberate rebuild, never automatic destruction.
The original proposed precision/time gates are not claimed passed from these
synthetic/backend tests alone.
