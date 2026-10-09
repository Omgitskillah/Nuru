Add opt-in, model-free vault stewardship to Nuru with a standalone CLI and shared
session lifecycle. Configured read-only reports cover the first deterministic
milestones of all eleven maintenance work packages. Background reports coalesce
changes and reconcile missed events; optional state remembers evidence-scoped
review decisions without editing notes. The new MCP tools are not added to the
default model tool list and the stewardship worker is disabled by default.

The linked [Obsidian PR #3](https://github.com/Omgitskillah/obsidian-4-llms/pull/3) owns parsers, facts, revisions, findings/state and index
maintenance. This PR pins its reviewed feature commit, adds reproducible fixtures,
evaluation protocols/raw results and individual milestone commits. CI runs the
dependency's tests at the exact pin; scale runs remain manual and model-free.

Validation: combined local suite 1,410 passed / 1 opt-in live test skipped; both
lockfiles checked. Supplemental fixture has 16/16 expected finding/review
occurrences. Ten repeated Nomic v2 index sequences and 45 query controls pass.
Qwen readiness: 95/100 structural validations, 90/100 useful proposals; advisory
prose quality fails, so interpretation remains experimental and default-off.
Concurrent embedding raises retrieval p95 30–41%, failing the <=10% gate; schedule
indexing outside interaction rather than claiming a successful background rollout.
Actual SQLite fault/restart/edit regressions pass. No notes in the original
DummyVault were changed. Human efficacy and personal-vault pilots remain pending.

The synthetic correctness/advisory/contention lanes reconstruct from tracked
inputs; the benchmark-copy lane explicitly requires the external DummyVault.
Earlier failures are retained, not pooled into newer-source accuracy claims.

Dependency ordering: merge the companion Obsidian backend PR first, repin this
PR to its actual main commit, then rerun integration checks. These are first
deterministic milestones, not completion of every original work package.
CI validates the published integration head and private dependency checkout.
Backend feature pin: `03724fa7c16e3b8250b799c2edf3f490d1c1ea7d`.
