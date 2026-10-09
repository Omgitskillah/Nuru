# WP01 — Property consistency

Status: deterministic first milestone implemented, 2026-10-08. See the
[milestone ledger](implementation-milestones.md) and
[offline evaluation](../../evals/vault_steward/steward-implementation-2026-10-08.md).
Optional model quality, held-out labels, and real-vault efficacy remain NOT RUN.

## Outcome

Reduce metadata mistakes that make project lists, searches, and Bases views incomplete or misleading.

## Code and model boundary

Code validates a user-declared schema. A language model may suggest missing values from evidence, but inference is optional and never treated as authoritative metadata.

## Implementation scope

- Define schemas by folder/type: required versus optional properties, types, allowed values, date formats, and cross-field rules.
- Parse frontmatter without losing duplicate-key errors or silently coercing invalid values. Respect vault-wide property types where configured; report configuration conflicts separately from note defects.
- Return a per-field finding with expected/actual value and source location. Support tag/value aliases as explicit user rules, not guessed equivalences.
- V1 reports only. Later deterministic normalization previews a minimal diff; inferred values remain suggestions. Do not rewrite entire frontmatter or change prose.

## Validation cases

1. Exercise missing fields, wrong types, invalid dates, duplicate keys, legacy property names, valid JSON frontmatter, Unicode values, optional omissions, and code-fenced examples.
2. Change a schema and verify affected notes are rechecked even when file bytes do not change. Reject invalid schemas rather than flagging every note.
3. Compare cold and incremental findings after edit/rename/delete. Verify unchanged content, stable finding IDs, suppression, and restart behavior.
4. Optional inference: request a value for a note with evidence, one with conflicting evidence, and one with none. Require citations or abstention.

Use the [test-vault specification](test-vault.md), fresh isolated copies, and the
[shared protocol](README.md#shared-validation-and-efficacy-protocol). For this
package verify no-change reruns, stable findings, event coalescing, concurrent edits,
restart, and missed-event recovery. V1 must leave authored files byte-identical.

## Efficacy and proposed acceptance gates

- Unit: (note, property, rule). Initial deterministic gate: all supported syntax fixtures correct, zero false defects on clean controls, and all seeded defects detected.
- Optional inference: proposed held-out gate of at least 95% precision for evidence-supported suggestions; report coverage and abstention so declining every case cannot pass as useful.
- Measure invalid fields found per 100 notes, review time per confirmed correction, and downstream list membership against the oracle before/after approved corrections.
- Attribute speed to incremental revalidation separately from model inference.

Thresholds above are initial proposals to agree before implementation; they are
not benchmark results. Publish case counts, failures, raw timings, coverage, and
resource measurements. Validate optional model stages separately from code-only
checks; an unavailable model must not stop deterministic bookkeeping.

## Dependencies and deliverables

WP01 supplies schema metadata to WP04, WP05, and WP06; no model dependency. Deliver schema format, read-only validation API, findings, and fixtures.

Complete with tool/configuration documentation, fixtures and evaluator-only
oracles, deterministic tests, a results note in `nuru/evals/vault_steward/`, and
known limitations. Apply the shared revision, receipt, undo, and uncertainty
contract before any later automatic mutation feature is enabled.
