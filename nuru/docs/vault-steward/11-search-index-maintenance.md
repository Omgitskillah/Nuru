# WP11 — Search-index maintenance

Status: proposed work package, 2026-10-07. No implementation or measured results.

## Outcome

Keep retrieval current while limiting background resource use and avoiding contamination between incompatible embedding configurations.

## Code and model boundary

Code handles change tracking, lexical indexing, deletion, and reconciliation. Semantic indexing requires an embedding model; generation is unnecessary.

## Implementation scope

- Extend the existing Obsidian semantic indexer rather than inventing a parallel index. Inspect its changed-note/deletion/configuration behavior before assigning new work.
- Track content hashes, paths, exclusions, parser/chunking versions, and embedding model digest/dimensions. A configuration change marks incompatible entries unusable until rebuilt; never mix vectors silently.
- Add debounced incremental work, restart-safe queues, periodic reconciliation, lock handling, and bounded inference batches. Keep readers usable through transactional updates or a validated replacement index.
- Separate lexical availability from semantic freshness. Expose queue age, pending/error counts, indexed revisions, and last successful reconciliation. Yield when interactive model work needs resources.

## Validation cases

1. Cover added/changed/deleted/renamed notes, identical content under a new path, changed exclusions, unchanged files, and missed events.
2. Change embedding model/configuration and chunking; verify incompatibility handling and rebuilding. Test corruption, locks, interrupted updates, and inference unavailable with a fake service.
3. Compare incremental state with a full rebuild on the same final vault: paths, revision hashes, chunks, configuration, and retrieval outputs within declared numerical tolerance.
4. Use a deterministic fake embedder for queue/state tests and a pinned real embedder only in the separate retrieval-quality lane. Keep every run's index isolated.

Use the [test-vault specification](test-vault.md), fresh isolated copies, and the
[shared protocol](README.md#shared-validation-and-efficacy-protocol). For this
package verify no-change reruns, stable findings, event coalescing, concurrent edits,
restart, and missed-event recovery. V1 must leave authored files byte-identical.

## Efficacy and proposed acceptance gates

- Deterministic index state must equal the rebuild oracle; zero stale deleted-note hits and zero mixed-configuration vectors.
- No-change runs must perform zero embedding calls. For one-note edits embed only affected chunks/notes unless documented global configuration changes require more.
- Measure freshness p50/p95, calls/tokens per edit, cold rebuild throughput, index bytes, lock/restart recovery, and worker-on interactive latency.
- Retrieval lane: compare recall@5 and ranked relevance before/after edits and against a full rebuild. A fake embedder does not establish semantic quality.

Thresholds above are initial proposals to agree before implementation; they are
not benchmark results. Publish case counts, failures, raw timings, coverage, and
resource measurements. Validate optional model stages separately from code-only
checks; an unavailable model must not stop deterministic bookkeeping.

## Dependencies and deliverables

Builds on servers/obsidian/src/obsidian_mcp/semantic.py. Supplies embeddings to WP05, WP07, WP08, and WP10. Deliver incremental worker, observable freshness, and rebuild-equivalence tests.

Complete with tool/configuration documentation, fixtures and evaluator-only
oracles, deterministic tests, a results note in `nuru/evals/vault_steward/`, and
known limitations. Apply the shared revision, receipt, undo, and uncertainty
contract before any later automatic mutation feature is enabled.
