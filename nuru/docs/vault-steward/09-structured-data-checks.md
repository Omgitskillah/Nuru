# WP09 — Structured-data checks

Status: proposed work package, 2026-10-07. No implementation or measured results.

## Outcome

Find numerical and tabular errors without asking a language model to perform routine arithmetic.

## Code and model boundary

Code parses declared table schemas, validates units, and computes totals. A language model may propose a schema or extract prose values; its output must pass code validation.

## Implementation scope

- Support a declared Markdown table subset with named columns, required cells, numeric/date formats, currencies/units, subtotal rules, and explicit tolerances.
- Distinguish absent, zero, invalid, and not-applicable values. Use decimal arithmetic for money; avoid live exchange rates unless a versioned rate table is supplied.
- Compute derived values in sidecar reports, with input-cell evidence and formulas. Treat formula strings and embedded code as data, never executable content.
- Optional extraction retains raw text and provenance alongside parsed values. V1 flags errors rather than overwriting notes or inventing missing data.

## Validation cases

1. Test escaped pipes, code spans, alignment rows, Unicode symbols, locale separators under declared locale, negatives, percentages, mixed units, and malformed rows.
2. Include correct and incorrect subtotals, rounded results inside/outside tolerance, missing cells, zero values, and duplicate rows.
3. Verify recomputed numerical oracles independently from the implementation. Unsupported syntax yields a coverage warning, not fabricated arithmetic.
4. Optional extraction cases include ambiguous units and uncertain prose; require abstention or an explicit unresolved value.

Use the [test-vault specification](test-vault.md), fresh isolated copies, and the
[shared protocol](README.md#shared-validation-and-efficacy-protocol). For this
package verify no-change reruns, stable findings, event coalescing, concurrent edits,
restart, and missed-event recovery. V1 must leave authored files byte-identical.

## Efficacy and proposed acceptance gates

- Unit: cell/rule and table total. Require exact results for supported integer/decimal cases or the fixture's explicit numerical tolerance.
- Zero false errors on valid rounded totals; detect every seeded supported-schema defect. Report parsing coverage separately from defect recall.
- Optional extraction: proposed at least 95% correctly parsed, evidence-supported values; report unresolved values and downstream errors.
- Measure time to validate a dataset manually versus with the report, numerical errors missed, and scan resource use.

Thresholds above are initial proposals to agree before implementation; they are
not benchmark results. Publish case counts, failures, raw timings, coverage, and
resource measurements. Validate optional model stages separately from code-only
checks; an unavailable model must not stop deterministic bookkeeping.

## Dependencies and deliverables

Uses WP01 schema conventions; no model required. Deliver supported table grammar, validation/calculation API, evidence locations, and independently calculated fixtures.

Complete with tool/configuration documentation, fixtures and evaluator-only
oracles, deterministic tests, a results note in `nuru/evals/vault_steward/`, and
known limitations. Apply the shared revision, receipt, undo, and uncertainty
contract before any later automatic mutation feature is enabled.
