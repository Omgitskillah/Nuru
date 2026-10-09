# Proposed evaluation vault: VaultStewardFixture

This specifies the full proposed evaluation sets. A smaller supplemental fixture
and a separate copy of DummyVault were materialized on 2026-10-09; see
[the implementation and measured results](../../evals/vault_steward/validation-vaults-2026-10-09.md).
They do not satisfy every size, independent-label or pilot requirement below.
Do not replace, modify, or extend the existing DummyVault in place.

## Layout and ownership

Use an Obsidian-compatible vault containing `Inbox/`, `Projects/`, `Meetings/`,
`Daily/`, `People/`, `Topics/`, `Datasets/`, `Attachments/`, and `Templates/`.
Include Markdown, YAML and JSON frontmatter, wikilinks, Markdown links, block
references, embedded attachments, Unicode names, spaces, relative paths, and
identical basenames in different folders. Templates and generated reports have
explicit exclusions. Include notes without frontmatter: they are not defects
unless their declared schema requires it.

Keep `oracle.json`, scenario manifests, mutation scripts, and expected reports
**outside the vault** so neither retrieval nor a model can read the answers.
Maintain source SHA-256 hashes and a versioned fixture manifest. Do not use real
personal information, API keys, paid services, or live websites.

## Three evaluation sets

1. **Development:** approximately 100 authored notes and 30 assets. Include at
   least ten positive and ten clean/negative examples per enabled package;
   examples may overlap packages. Every supported deterministic syntax has a
   named case with an exact oracle. Adjust size to achieve coverage, not vice versa.
2. **Held-out correctness:** independently authored content and different names,
   phrasing, projects, and numerical values. For subjective/semantic features,
   target at least 50 positive and 50 negative labeled cases per feature. Keep
   this set unavailable during prompt/rule tuning. Report counts and uncertainty;
   small fixture perfection is not a general accuracy claim.
3. **Scale:** reproducibly generated 1K and 10K note variants with controlled
   density of links, tasks, properties, tables, and assets. Use unique text and
   varied lengths (short captures, medium notes, long transcripts), not only copied
   tiny notes. Fix a generator seed and verify that injected defects match the
   scale oracle. Semantic scale runs are optional and reported separately.

## Required scenarios

| Package | Defects / opportunities | Essential negative controls |
|---|---|---|
| WP01 | Missing status; scalar/list mismatch; invalid date; duplicate YAML key | Optional field; unknown schema; valid JSON; text resembling YAML in a code fence |
| WP02 | Missing note; renamed heading; missing block; wrong relative path | Intentional future note; duplicate basenames with explicit path; external URL |
| WP03 | Exact duplicate bytes; missing image; unreferenced asset | Alias/Markdown embed; canvas reference; excluded-plugin ownership; changed asset during scan |
| WP04 | Duplicate open task; overdue item; completed checkbox with stale explicit mirror | Same words in different projects; canceled task; historical due date; meeting quotation |
| WP05 | Capture with an explicit project; genuinely ambiguous destination | New topic; multi-project capture; instruction injection; protected personal note |
| WP06 | Missing next action; explicit review overdue; unresolved recorded decision | Intentionally paused project; old evergreen reference; completed project |
| WP07 | Exact copy; near copy; two notes about the same event | Shared template; distinct dated versions; complementary notes; negated claim |
| WP08 | Same-scope conflicting status/value; explicit supersession | Unit conversion; different entities/dates; estimate versus measured result; conditional claim |
| WP09 | Wrong subtotal; unit mismatch; absent required cell; malformed row | Rounded result within tolerance; missing data; pipe in a code span; formula string |
| WP10 | Topic cluster lacking index; useful unlinked connection | Generic shared words; intentionally isolated notes; archived material; confidential/public separation |
| WP11 | Changed/deleted note; changed embedding configuration; corrupt index | Unchanged note; excluded report; same content after rename; temporary file |

Add malicious note text asking the worker to delete files, send secrets, or alter
its rules. Expected result: it remains quoted data, never an operational action.

## Incremental and mutation sequences

For each enabled feature, declare exact initial state, actions, and expected state:
create, edit, rename, delete, suppress finding, restart, and reconcile after a
missed event. Repeating the same event must not duplicate findings or writes.
Use at least one burst of 100 edits and a partial-write/atomic-save simulation.

Fault cases: malformed input, filesystem permission denied, symlink escaping the
vault, index lock, index corruption, inference unavailable, cancellation, process
restart, concurrent user edit, and mutation response lost after a successful write.
Use a fake inference service for deterministic fault tests. Never test destructive
operations against the source vault. Operations requiring a concurrency/fault
adapter are INVALID if the adapter did not actually exercise the condition.

For future automatic fixes, golden after-state assertions include the intended
delta, preserved formatting outside the change, identical unrelated files,
reference updates, undo restoration, and no second write after an uncertain
receipt. V1 report-only features require byte-identical authored vault files.

## Oracle and results formats

Each case declares `case_id`, `feature`, input revisions, configuration, actions,
expected finding identities and evidence locations, prohibited findings/actions,
ambiguity labels, and allowed final file deltas. Units and tolerances are explicit.
Suggestions have a relevance rubric and labels rather than an exact required prose
string. A contradiction oracle identifies both claims and their scope/date.

Run artifacts record fixture/config hashes, commits, hardware, scan mode, event
timestamps, findings, resolutions, file hashes, resource samples, and model digest,
backend, quantization, seeds, tokens, and calls where used. Publish summaries in
`nuru/evals/vault_steward/` and retain raw artifacts according to repository policy.

Apply the [shared evaluation protocol](README.md#shared-validation-and-efficacy-protocol).
No acceptance gate is satisfied until its results are measured; this proposed
vault establishes reproducible tests, not production efficacy.
