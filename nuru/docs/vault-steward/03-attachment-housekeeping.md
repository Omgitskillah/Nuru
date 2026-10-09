# WP03 — Attachment housekeeping

Status: deterministic first milestone implemented, 2026-10-08. See the
[milestone ledger](implementation-milestones.md) and
[offline evaluation](../../evals/vault_steward/steward-implementation-2026-10-08.md).
Optional model quality, held-out labels, and real-vault efficacy remain NOT RUN.

## Outcome

Expose storage waste and missing assets while avoiding removal of files that are used through less obvious references.

## Code and model boundary

Code inventories sizes, hashes, and known references. Optional vision or embedding models can suggest visually similar assets; they are unnecessary for exact duplicates.

## Implementation scope

- Hash asset bytes, group exact duplicates, locate missing embeds, and report large files using configurable thresholds.
- Trace Markdown, wikilink/embed, frontmatter, and declared canvas references. Report unknown plugin references as unknown ownership, not proof that an asset is unused.
- Exclude symlinks escaping the vault. Revalidate size/hash if a file changes during scanning. Bound hashing concurrency and avoid loading huge files into memory.
- V1 creates an inventory and review queue. Future cleanup first quarantines approved files with an undo manifest; update references before consolidating duplicates. No automatic deletion in this package.

## Validation cases

1. Create duplicate bytes under different names and different bytes under identical names; assert correct grouping and saved-byte calculation.
2. Include assets referenced only by frontmatter, Markdown aliases, canvas, and a declared unsupported plugin; verify ownership classifications.
3. Simulate concurrent asset changes, permission errors, inaccessible files, and vault-escaping symlinks. Scan twice with no duplicate findings.
4. For a later approved consolidation, verify every reference and byte-identical restore from quarantine.

Use the [test-vault specification](test-vault.md), fresh isolated copies, and the
[shared protocol](README.md#shared-validation-and-efficacy-protocol). For this
package verify no-change reruns, stable findings, event coalescing, concurrent edits,
restart, and missed-event recovery. V1 must leave authored files byte-identical.

## Efficacy and proposed acceptance gates

- Exact duplicate groups and missing references must match the oracle; zero deletion eligibility claims for assets with unknown ownership.
- Report potential reclaimable bytes once per duplicate group, excluding its retained copy. Distinguish potential from actually reclaimed storage.
- Measure hashing throughput, bytes reread on no-change scans, peak memory, and time to review a cleanup batch.
- Optional visual similarity is a separate lane: judge pair relevance and acceptance; never equate visual similarity with byte interchangeability.

Thresholds above are initial proposals to agree before implementation; they are
not benchmark results. Publish case counts, failures, raw timings, coverage, and
resource measurements. Validate optional model stages separately from code-only
checks; an unavailable model must not stop deterministic bookkeeping.

## Dependencies and deliverables

Depends on WP02 for reference coverage. Deliver asset inventory, hash cache, ownership classifications, duplicate groups, and reviewable cleanup plans.

Complete with tool/configuration documentation, fixtures and evaluator-only
oracles, deterministic tests, a results note in `nuru/evals/vault_steward/`, and
known limitations. Apply the shared revision, receipt, undo, and uncertainty
contract before any later automatic mutation feature is enabled.
