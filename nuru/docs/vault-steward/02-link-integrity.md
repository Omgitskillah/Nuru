# WP02 — Link integrity

Status: deterministic v1 implemented on `feat/vault-link-integrity`, 2026-10-07.
See [usage and evaluation](../../evals/vault_steward/README.md). The shared
stewardship runner now adds optional persistent acknowledgements and conservative
rename identity; the original `audit_links` tool remains stateless. Model-assisted
ranking quality, held-out evaluation, and real-vault efficacy remain follow-up
work; source notes are never automatically repaired.

## Outcome

Find references that no longer reach the intended note or section without labeling deliberate future notes as errors.

## Code and model boundary

Code parses and resolves supported references. A language model can rank possible replacements, but cannot decide silently which duplicate basename was intended.

## Implementation scope

- Support wikilinks and Markdown links, aliases, URL encoding, embeds, note paths, headings, and block IDs. Define supported resolution semantics and distinguish vault-root paths from relative Markdown paths.
- Build a reverse-reference index and distinguish resolved, broken, ambiguous, intentional-unwritten, excluded, and unsupported references.
- Match heading/block existence in parsed Markdown rather than plain text alone; ignore examples inside fenced code.
- Propose repair candidates with evidence. V1 does not rename or relink files. A future rename operation must update references transactionally enough to recover partial completion without Obsidian running.

## Validation cases

1. Cover duplicate basenames, same heading repeated, Unicode, spaces, encoded paths, aliases, explicit folder paths, local anchors, block embeds, and case-sensitive filesystem behavior.
2. Rename a note or heading; compare all incoming references with the oracle. Test target creation resolving an intentionally unwritten reference.
3. Delete and recreate a target, change exclusions, miss an event, and reconcile. Do not fetch external URLs during filesystem validation.
4. Optional replacement ranking: include plausible but wrong similarly titled notes and record top-1 and top-3 correctness.

Use the [test-vault specification](test-vault.md), fresh isolated copies, and the
[shared protocol](README.md#shared-validation-and-efficacy-protocol). For this
package verify no-change reruns, stable findings, event coalescing, concurrent edits,
restart, and missed-event recovery. V1 must leave authored files byte-identical.

## Efficacy and proposed acceptance gates

- Unit: source reference occurrence. Require complete detection of seeded broken references and zero confirmed defects for deliberate placeholders and supported valid references.
- Measure resolver coverage across syntax categories, ambiguity counts, detection delay, and reverse-index lookup time.
- Optional suggestions: proposed top-1 precision at least 95%; report abstention and top-3 recall separately. No ambiguous automatic repair.
- Pilot: compare time to locate and repair the same number of genuine broken references, including false suggestions reviewed.

Thresholds above are initial proposals to agree before implementation; they are
not benchmark results. Publish case counts, failures, raw timings, coverage, and
resource measurements. Validate optional model stages separately from code-only
checks; an unavailable model must not stop deterministic bookkeeping.

## Dependencies and deliverables

WP02 provides reference resolution to WP03 and WP10. Deliver resolver contract, reverse index, finding taxonomy, and link fixtures.

Complete with tool/configuration documentation, fixtures and evaluator-only
oracles, deterministic tests, a results note in `nuru/evals/vault_steward/`, and
known limitations. Apply the shared revision, receipt, undo, and uncertainty
contract before any later automatic mutation feature is enabled.
