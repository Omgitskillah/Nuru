# PR readiness execution

## M1: integration baseline (2026-10-09)

Fetched both origins and rebased without conflicts. Nuru baseline:
`fc1f0409752d6a99c633eff7372f8c7cd7787c39`. Obsidian baseline:
`3e084e30b9614b06e415c2de39f58553051f686e`. Obsidian feature head and
parent pin remain `53555f3cfb97860b0cf5e481489fda482bbe5e01`.
No open Nuru PR existed at inspection. Existing milestone commits were preserved.
The isolated offline suite passed: **1,382 passed, 1 skipped**, 11.68 seconds.
The skip is the historical opt-in live embedding test. No inference was used.

Later evidence records its own source hashes; earlier model results remain
historical and are not pooled with this release's evaluation.

## M2–M3: parsed evidence and constrained interpretation

Backend milestones `587e096` / `711a572` introduce `maintenance-evidence-v1`
and `VALIDATED_EVIDENCE_AND_CONSTRAINTS`. Actual live references, all active task
mirrors, declared table arithmetic, attachment ownership limits, scoped claims
and navigation edges provide bounded facts. Evidence IDs replace model-written
quotations. Unknown/missing IDs and unsupported proposal fields fail; citations,
revision checks and factual statements stay code-owned. Default inference is off.

## M4: frozen local advisory evaluation

Ten development and ten fresh-v1 controls, five Qwen 3.5 4B repetitions: 95/100
structural validations, 90/100 useful proposals. Separate raw-prose inspection:
85 full, 10 partial, 5 false (assistant labels only). Source bytes unchanged.
**Advisory quality FAIL**, retained as experimental. No tuning on the inspected
fresh set and no thinking-on rerun. See [protocol/results](readiness-advisory-2026-10-09.md).
Final stale-asset/coverage guards preserve all original candidate inputs and
outcomes in a 100-response cached replay with zero inference calls.

## M5: index faults and measured contention

Backend milestone `20f1b6e` fixes reproduced stale publication during embedding
and unrelated-database modification. Actual lock/corruption/batch failure,
concurrent edit, root ownership and active worker restart checks pass. Repeated
Nomic v2 index acceptance passes all ten sequences and all 45 query controls.

Live retrieval contention **FAILS** the <=10% p95 gate in all five repeats:
median ratio 1.361, range 1.298–1.408. All 200 retrievals find the expected note;
worker stop/recovery and no-change zero-call checks pass. No overlapping rollout
is recommended; schedule embedding maintenance outside interactive use. Existing
indexer defaults are preserved. See [faults/contention evidence](readiness-index-2026-10-09.md).

## M6: local release review

Reviewed default-off integration, read-only contracts, owned/excluded state,
symlinks, revisions, cancellation, scoped evidence, schemas, dependency pin and
CI configuration. Added database/WAL/SHM symlink rejection and stale-asset plus
incomplete-evidence guards with deterministic regressions. No new model tools,
authored-note writes, background inference adapter or destructive recovery.

Full isolated suite: **1,410 passed, 1 skipped**, 12.52 seconds. Both lockfiles
pass `uv lock --check --offline` (104 packages). Both CLIs reproduce the synthetic
oracle (16/16), and real MCP list/call smoke checks pass without inference.
Clean source archives of both pinned trees also pass the complete suite:
1,410 passed / 1 skipped, 12.72 seconds, plus both CLI and MCP smoke checks.
They contain no ignored local vaults, indexes or env files. The existing locked
local venv supplies dependencies; this is not a fresh installation or Linux CI.
See [clean-checkout artifact](readiness-clean-checkout-2026-10-09.json).
Fixture, benchmark copy and all copied authored source hashes match their
manifest. Whitespace checks pass. No actual env, local vault or SQLite dump is
staged.

Both repositories are private. Nuru has the existing `OBSIDIAN_REPO_TOKEN` secret
configured (metadata presence checked, no value read); CI still needs an actual
published run to verify access. Independent human labels/personal-vault efficacy
are deferred; failed quality/contention gates remain explicit release limitations.

## M7: authorized publication

Descriptions are ready: [backend PR](https://github.com/Omgitskillah/obsidian-4-llms/pull/3) and
[Nuru PR](https://github.com/Omgitskillah/Nuru/pull/11). Target main in both repos, backend first; repin Nuru
to the eventual backend merge commit and rerun integration before Nuru merging.
The user authorized publication with "Publish". Obsidian's branch was pushed
as a fast-forward; Nuru's rebase was pushed with an explicit lease against
`e78e1461a7d8dc63b412a2cd8a0fbc3aa5574623`. Both PRs target main:
[Obsidian #3](https://github.com/Omgitskillah/obsidian-4-llms/pull/3) and
[Nuru #11](https://github.com/Omgitskillah/Nuru/pull/11).
Nuru pins backend `03724fa7c16e3b8250b799c2edf3f490d1c1ea7d`.
Backend CI passed on that head. GitHub push/PR events triggered the configured
test workflows; no paid model or manual scale job was started. Final Nuru CI is
checked after the publication record commit. No merge was performed.

## Review-fix follow-up

After PR10 merged, PR11 was rebased onto main 8c47624. The [review-fix report](review-fixes-2026-10-09.md)
records macOS fixes, archived evidence, 1,507 passing integrated tests (one skipped),
and the remaining actual-backend-merge repin. Windows concerns are deferred at the
user's request. Historical live contention evidence remains failed.

Obsidian PR3 merged as 70131275727385355bd60047d8467c8653df5e6f; Nuru now
pins that exact main commit. The integrated suite passes again (1,507 passed,
one skipped), and fixture CLI/MCP plus offline lockfile checks pass.
