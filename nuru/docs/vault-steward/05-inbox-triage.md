# WP05 — Inbox triage

Status: deterministic first milestone implemented, 2026-10-08. See the
[milestone ledger](implementation-milestones.md) and
[offline evaluation](../../evals/vault_steward/steward-implementation-2026-10-08.md).
Optional model quality, held-out labels, and real-vault efficacy remain NOT RUN.

## Outcome

Reduce time spent organizing captures while allowing unfamiliar or ambiguous material to remain unclassified.

## Code and model boundary

Code applies explicit folder/tag/project rules. Embeddings suggest destinations; a language model can explain and propose tags or splits.

## Implementation scope

- Detect captures in user-designated inbox locations. Route explicit metadata deterministically before invoking interpretation.
- Rank existing destinations using bounded title/content evidence and permitted scope. Include leave-in-inbox and create-new-topic as valid outcomes.
- Return destination, proposed metadata, supporting note links, and uncertainty. Do not infer sharing permissions from topical similarity.
- V1 previews a batch. Later approved moves preserve note content and repair references; splitting a capture is a separate reviewed content edit.

## Validation cases

1. Include explicit-project captures, clear implicit associations, multi-project captures, novel subjects, and notes with insufficient context.
2. Ensure personal/protected notes are not routed to public/shared folders. Inject instructions inside captures and verify they cannot change routing rules.
3. Rename a destination between suggestion and acceptance; require revision/path revalidation rather than a stale move.
4. Grade held-out captures with acceptable destination sets and explicit ambiguity labels, not one forced folder for every note.

Use the [test-vault specification](test-vault.md), fresh isolated copies, and the
[shared protocol](README.md#shared-validation-and-efficacy-protocol). For this
package verify no-change reruns, stable findings, event coalescing, concurrent edits,
restart, and missed-event recovery. V1 must leave authored files byte-identical.

## Efficacy and proposed acceptance gates

- Code rules: exact expected routes and zero unsupported moves. Model suggestion gate: proposed at least 90% acceptable top-1 destinations on unambiguous held-out captures.
- Report top-3 relevance, coverage, ambiguity handling, accepted-without-edit rate, and corrected suggestions separately.
- Pilot gate proposal: at least 20% less human triage time on matched batches with no increase in misfiling; measure before declaring success.
- Track total tokens/calls and time per accepted capture, including review and reference updates.

Thresholds above are initial proposals to agree before implementation; they are
not benchmark results. Publish case counts, failures, raw timings, coverage, and
resource measurements. Validate optional model stages separately from code-only
checks; an unavailable model must not stop deterministic bookkeeping.

## Dependencies and deliverables

Depends on WP01 metadata and WP02 for future moves; semantic candidates use WP11. Deliver configurable inbox rules and a reviewable routing queue.

Complete with tool/configuration documentation, fixtures and evaluator-only
oracles, deterministic tests, a results note in `nuru/evals/vault_steward/`, and
known limitations. Apply the shared revision, receipt, undo, and uncertainty
contract before any later automatic mutation feature is enabled.
