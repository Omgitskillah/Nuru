# WP04 — Task reconciliation

Status: deterministic first milestone implemented, 2026-10-08. See the
[milestone ledger](implementation-milestones.md) and
[offline evaluation](../../evals/vault_steward/steward-implementation-2026-10-08.md).
Optional model quality, held-out labels, and real-vault efficacy remain NOT RUN.

## Outcome

Give a consistent account of open work across meeting, daily, and project notes without inventing completion.

## Code and model boundary

Code handles declared checkbox/status/deadline syntax. Embeddings or a language model may connect differently worded tasks and identify prose evidence of completion.

## Implementation scope

- Define supported task syntax, timezone, reference clock, status mapping, recurrence handling, and project association. Unsupported plugin syntax remains explicit.
- Extract task occurrences with source revision and stable anchors. Distinguish a task identity from repeated mentions or historical snapshots.
- Flag overdue open tasks and conflicting explicit mirrors. Candidate semantic matches must share compatible project/entity context.
- Suggest reconciliation with both sources visible; do not mark tasks complete merely because similar prose sounds positive. Do not create reminders or external messages.

## Validation cases

1. Freeze the evaluation clock. Include overdue/open, future, canceled, completed, recurring, undated, and quoted tasks; verify each classification.
2. Use identical wording in different projects and different wording for one task. Test completion followed by explicit reopening.
3. Preserve historical meeting records while suggesting a change only to the active task source. Test deadline edits and restart.
4. Optional model tests require cited completion/reopening evidence; include speculative and negated completion language.

Use the [test-vault specification](test-vault.md), fresh isolated copies, and the
[shared protocol](README.md#shared-validation-and-efficacy-protocol). For this
package verify no-change reruns, stable findings, event coalescing, concurrent edits,
restart, and missed-event recovery. V1 must leave authored files byte-identical.

## Efficacy and proposed acceptance gates

- Unit: task identity and status conflict. Exact extraction and overdue classification must pass all declared syntax fixtures.
- Semantic matching/completion suggestions: proposed precision at least 95%, with recall, coverage, and project-crossing false matches reported. Zero incorrect automatic completions.
- Measure stale open tasks surfaced, duplicates reviewed per true duplicate, and manual versus assisted reconciliation time.
- Include extraction and interpretation latency in cumulative time-to-correct-task-state.

Thresholds above are initial proposals to agree before implementation; they are
not benchmark results. Publish case counts, failures, raw timings, coverage, and
resource measurements. Validate optional model stages separately from code-only
checks; an unavailable model must not stop deterministic bookkeeping.

## Dependencies and deliverables

Uses WP01 schema rules; optionally WP07 candidate matching. Deliver task ledger, explicit identity/status semantics, reconciliation findings, and clock-controlled fixtures.

Complete with tool/configuration documentation, fixtures and evaluator-only
oracles, deterministic tests, a results note in `nuru/evals/vault_steward/`, and
known limitations. Apply the shared revision, receipt, undo, and uncertainty
contract before any later automatic mutation feature is enabled.
