Vault maintenance currently requires separate manual checks. This change adds
read-only, configured reports for properties, live links, attachments, explicit
task mirrors, declared tables, project reviews, inbox routing previews, duplicate
copies, structured claims and navigation graphs, usable without Obsidian running.

CLI/MCP reports include revisions and bounded evidence. Optional excluded SQLite
state tracks review decisions and conservative rename identity; notes are never
repaired automatically. An injectable experimental interpretation boundary uses
parser-owned evidence IDs and validated proposal objects, with exact citations
attached by code. It cannot infer completion from conflicting mirrors or replace
computed totals with authored incorrect values. Advisory prose still needs review.

Index maintenance preserves per-note transactions, persists pending/error state,
rejects unrelated/symlinked databases and cross-vault reuse, and refuses stale
publication when a note changes during embedding. Corruption requires explicit
rebuild; no automatic destructive recovery is introduced.

Validation: combined local offline suite 1,410 passed / 1 opt-in live test skipped;
actual SQLite lock/corruption, embedding failure, worker restart and concurrent-edit
regressions pass. Nomic v2 passes ten isolated index sequences and 45 selected
retrieval controls. Qwen 3.5 4B passes 95/100 structural and 90/100 proposal controls,
but advisory prose quality still fails. Retrieval contention fails the <=10% p95
gate (30–41% increase); overlapping embedding maintenance is not recommended.
Independent human efficacy and personal-vault pilots remain unmeasured.

This is the first deterministic milestone of each work package, not completion
of all proposed semantic/plugin features. No production model adapter, autonomous
repair, semantic duplicate search or generated connection feature is included.
The companion Nuru PR contains worker integration, evaluation protocols/raw
results, default-off settings and the exact dependency pin. Merge this backend
first, then repin Nuru to its actual main commit. CI/publication status will be
updated once the two PRs are created.
