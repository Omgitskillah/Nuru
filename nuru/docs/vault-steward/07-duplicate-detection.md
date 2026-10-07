# WP07 — Duplicate detection

Status: proposed work package, 2026-10-07. No implementation or measured results.

## Outcome

Find redundant notes while preserving distinct versions, complementary accounts, and records with different dates or scope.

## Code and model boundary

Code finds identical content and lexical overlap. Embeddings retrieve semantic candidates; an optional language model classifies overlap and drafts a merge proposal.

## Implementation scope

- Define exact-byte and normalized-text equivalence separately. Do not hide meaningful frontmatter differences through normalization.
- Use lexical fingerprints and optional embeddings to generate bounded candidate pairs. Classify exact copy, likely redundant, complementary, versioned, and uncertain.
- Return both passages and metadata differences. Similar titles or shared templates alone are not duplicate evidence.
- V1 reports groups; no deletion or merge. Future merge plans preserve unique facts, source links, history, and inbound references before archiving originals.

## Validation cases

1. Cover identical bytes, same prose with different metadata, near copies, shared templates, successive dated versions, and complementary notes.
2. Include semantically close notes whose negation or numerical value differs. They must not be collapsed as interchangeable copies.
3. Measure candidate retrieval independently from the classifier using held-out pair labels. Ensure thresholds are frozen before held-out evaluation.
4. Verify clustering does not imply all members are pairwise interchangeable; inspect nontransitive A/B/C similarity cases.

Use the [test-vault specification](test-vault.md), fresh isolated copies, and the
[shared protocol](README.md#shared-validation-and-efficacy-protocol). For this
package verify no-change reruns, stable findings, event coalescing, concurrent edits,
restart, and missed-event recovery. V1 must leave authored files byte-identical.

## Efficacy and proposed acceptance gates

- Exact-copy grouping must match all fixture oracles. For semantic duplicates propose at least 95% pair precision and 80% recall on held-out eligible pairs.
- Report candidate-stage recall, final pair precision/recall, group purity, coverage, and false flags on versioned/complementary controls.
- Measure comparisons avoided versus all-pairs baseline, tokens per confirmed group, and reviewer time per accepted consolidation plan.
- Do not report bytes saved until an approved consolidation is actually performed and validated.

Thresholds above are initial proposals to agree before implementation; they are
not benchmark results. Publish case counts, failures, raw timings, coverage, and
resource measurements. Validate optional model stages separately from code-only
checks; an unavailable model must not stop deterministic bookkeeping.

## Dependencies and deliverables

Uses WP11 for embeddings and WP02 for future merge references. Deliver candidate retrieval, pair/group taxonomy, evidence-backed reports, and pair-labeled fixtures.

Complete with tool/configuration documentation, fixtures and evaluator-only
oracles, deterministic tests, a results note in `nuru/evals/vault_steward/`, and
known limitations. Apply the shared revision, receipt, undo, and uncertainty
contract before any later automatic mutation feature is enabled.
