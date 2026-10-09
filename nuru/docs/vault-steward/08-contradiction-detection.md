# WP08 — Contradiction detection

Status: deterministic first milestone implemented, 2026-10-08. See the
[milestone ledger](implementation-milestones.md) and
[offline evaluation](../../evals/vault_steward/steward-implementation-2026-10-08.md).
Optional model quality, held-out labels, and real-vault efficacy remain NOT RUN.

## Outcome

Identify potentially conflicting claims about the same subject without flattening chronology, uncertainty, or conditional statements.

## Code and model boundary

Code compares declared structured fields after explicit unit normalization. A language model is needed for broad prose claim extraction and contextual comparison.

## Implementation scope

- Start narrowly with configured fields/entities, dates, units, scope, and provenance. Do not compare unrelated numbers merely because they share a label.
- Optional prose stage extracts claims with quoted evidence and qualifiers, then compares candidates concerning the same entity and scope.
- Classify conflict, superseded fact, different scope, estimate versus observation, and uncertain. Never decide which source is true without evidence.
- Report both sources, revisions, dates, normalized values, and reasoning. V1 does not rewrite either claim or promote the newest note automatically.

## Validation cases

1. Include equal values in different units, incompatible same-scope values, different devices, different dates, estimates versus measurements, and conditional claims.
2. Include explicit corrections, negation, historical quotations, unresolved alternatives, and later reversal of a decision.
3. Separate claim-extraction recall, candidate matching, and conflict-classification scoring; use adjudicated human labels for prose cases.
4. Inject contradictory instructions into notes and verify the report does not treat those instructions as worker commands.

Use the [test-vault specification](test-vault.md), fresh isolated copies, and the
[shared protocol](README.md#shared-validation-and-efficacy-protocol). For this
package verify no-change reruns, stable findings, event coalescing, concurrent edits,
restart, and missed-event recovery. V1 must leave authored files byte-identical.

## Efficacy and proposed acceptance gates

- Structured comparisons must pass all declared scope/unit fixtures. Prose conflict gate proposal: at least 95% precision and 70% recall on labeled resolvable conflicts.
- Report false conflicts by cause, abstentions, claim extraction coverage, and missed conflicts. Semantic overlap alone cannot count as a contradiction.
- Measure reviewer time to establish whether a conflict exists, correction requests, and tokens per confirmed conflict.
- Zero silent factual corrections; accuracy is evidence-backed classification, not choosing a winner.

Thresholds above are initial proposals to agree before implementation; they are
not benchmark results. Publish case counts, failures, raw timings, coverage, and
resource measurements. Validate optional model stages separately from code-only
checks; an unavailable model must not stop deterministic bookkeeping.

## Dependencies and deliverables

Uses WP01 structured fields and WP11 candidate retrieval; WP07 can supply overlap candidates. Deliver a scoped claim representation, comparison rules, and two-source findings.

Complete with tool/configuration documentation, fixtures and evaluator-only
oracles, deterministic tests, a results note in `nuru/evals/vault_steward/`, and
known limitations. Apply the shared revision, receipt, undo, and uncertainty
contract before any later automatic mutation feature is enabled.
