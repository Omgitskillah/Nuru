# WP06 — Project review preparation

Status: proposed work package, 2026-10-07. No implementation or measured results.

## Outcome

Prepare an evidence-backed project review that highlights missing next actions and unresolved decisions without mistaking inactivity for failure.

## Code and model boundary

Code uses explicit project states, review dates, and task metadata. A language model optionally summarizes progress and extracts unresolved decisions from prose.

## Implementation scope

- Define active/paused/completed states and review cadence per project. Require an explicit reference clock.
- Gather bounded project-linked tasks, recent changes, and recorded decisions. Produce sections for current state, next action, review due, and evidence gaps.
- Separate explicit overdue review from heuristic lack of recent edits. Old evergreen references and intentionally paused projects are not stale by default.
- Generate one review digest; do not change source status or invent next actions. Decisions must cite their source and remain unresolved unless later evidence resolves them.

## Validation cases

1. Cover active project without a next action, paused project, completed project, future review date, overdue review date, and old evergreen material.
2. Include proposed, adopted, rejected, superseded, and reopened decisions spread across notes with dates and scope.
3. Compare code-only checklist with optional summary on identical snapshots; verify every factual summary statement against evidence.
4. Edit a status during digest preparation and label stale evidence or regenerate. Exclude generated digests from future scans.

Use the [test-vault specification](test-vault.md), fresh isolated copies, and the
[shared protocol](README.md#shared-validation-and-efficacy-protocol). For this
package verify no-change reruns, stable findings, event coalescing, concurrent edits,
restart, and missed-event recovery. V1 must leave authored files byte-identical.

## Efficacy and proposed acceptance gates

- Deterministic review flags must exactly match explicit metadata oracles. Measure completeness of required review fields and correct project association.
- Optional summary: proposed at least 95% supported factual claims and 90% recall of labeled unresolved decisions; report omissions and invented claims independently.
- Measure human review time, missed decisions, corrections needed, and useful actions identified. Target a 20% time reduction without more missed issues in the pilot.
- Report preparation latency/tokens separately from human time-to-complete-review.

Thresholds above are initial proposals to agree before implementation; they are
not benchmark results. Publish case counts, failures, raw timings, coverage, and
resource measurements. Validate optional model stages separately from code-only
checks; an unavailable model must not stop deterministic bookkeeping.

## Dependencies and deliverables

Uses WP01 and WP04; WP02 resolves evidence links. Deliver project review rules, digest format, supported-claim grading, and fixed-clock cases.

Complete with tool/configuration documentation, fixtures and evaluator-only
oracles, deterministic tests, a results note in `nuru/evals/vault_steward/`, and
known limitations. Apply the shared revision, receipt, undo, and uncertainty
contract before any later automatic mutation feature is enabled.
