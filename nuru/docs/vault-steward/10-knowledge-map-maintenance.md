# WP10 — Knowledge-map maintenance

Status: proposed work package, 2026-10-07. No implementation or measured results.

## Outcome

Help users discover useful connections and navigate a growing vault without filling it with generic or unwanted links.

## Code and model boundary

Code builds link graphs, tag indexes, and disconnected-note reports. Embeddings and an optional language model suggest meaningful connections and explain their value.

## Implementation scope

- Generate navigation indexes from explicit folders, tags, and properties first. Describe disconnected notes as a graph condition, not necessarily a defect.
- Optional clustering proposes topic indexes and a bounded number of unlinked connections. Preserve configured boundaries between confidential, shared, archived, and active material.
- Explain each suggested link using specific passages and a relation such as prerequisite, evidence, contrast, or continuation; avoid shared-word-only explanations.
- V1 produces a review list or generated index. Do not automatically insert bidirectional links or create an index for every cluster.

## Validation cases

1. Check graph edges against WP02 resolution. Include intentionally isolated notes, generic shared words, archived content, and privacy boundaries.
2. Use held-out topic clusters and adjudicated relevance labels for candidate links. Judge whether a link helps a declared retrieval task, not only topical similarity.
3. Re-run after links are accepted: suggestions should disappear without duplicating generated indexes or linking the report to itself.
4. Compare existing navigation, code-generated indexes, and semantic suggestions on the same fixed information-finding tasks.

Use the [test-vault specification](test-vault.md), fresh isolated copies, and the
[shared protocol](README.md#shared-validation-and-efficacy-protocol). For this
package verify no-change reruns, stable findings, event coalescing, concurrent edits,
restart, and missed-event recovery. V1 must leave authored files byte-identical.

## Efficacy and proposed acceptance gates

- Code graph/index membership must match explicit metadata and link oracles. Zero cross-boundary suggestions on protected fixtures.
- Semantic gate proposal: precision@5 at least 80% for judged useful suggestions; report candidate coverage and acceptance separately. Fewer than five suggestions use the actual denominator.
- Pilot: measure successful navigation tasks, time-to-correct-source, unnecessary links reviewed, and accepted useful connections.
- Track index growth and tokens per accepted connection; graph density alone is not evidence of efficacy.

Thresholds above are initial proposals to agree before implementation; they are
not benchmark results. Publish case counts, failures, raw timings, coverage, and
resource measurements. Validate optional model stages separately from code-only
checks; an unavailable model must not stop deterministic bookkeeping.

## Dependencies and deliverables

Depends on WP02 and optionally WP11. Deliver graph queries, generated-index rules, connection suggestions, and navigation-task grading.

Complete with tool/configuration documentation, fixtures and evaluator-only
oracles, deterministic tests, a results note in `nuru/evals/vault_steward/`, and
known limitations. Apply the shared revision, receipt, undo, and uncertainty
contract before any later automatic mutation feature is enabled.
