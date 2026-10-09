# Vault stewardship: plan for PRs into main

Date: 2026-10-09. Starting branch: `feat/vault-link-integrity`.

## Release scope and current evidence

Prepare a first stewardship release: the implemented deterministic backends,
shared report/state/worker integration, and search-index maintenance. Optional
model interpretation remains experimental and disabled by default. This release
does not claim completion of all eleven original work packages, arbitrary plugin
compatibility, autonomous repairs, or demonstrated real-user efficacy.

Current evidence is recorded in [the milestone ledger](implementation-milestones.md)
and [validation results](../../evals/vault_steward/validation-vaults-2026-10-09.md):

- The offline suite passes: 1,382 tests, with one historical live test skipped.
- The supplemental fixture matches 16 expected finding/review occurrences.
- Nomic v2 passes five index sequences on each of two isolated vaults and ranks
  the expected note first in all 45 selected query repetitions.
- Qwen advisory acceptance fails. Real quotations still accompany wrong task
  completion, wrong arithmetic, and irrelevant link advice; two other cases
  produce invalid citations. Decision-category matching is insufficient grading.
- These are development/supplemental controls, not independently human-labeled
  held-out acceptance. GitHub CI changes are committed locally but unpublished.

Use one commit per milestone. Backend changes belong in `servers/obsidian`;
Nuru integration, dependency pins, evaluation artifacts and documentation belong
in the parent repository. Preserve existing local work and the original
`../DummyVault`; mutations and fault injection use disposable copies only.

## M1 — Update the integration baseline

Inspect both repositories' status, remotes, current main branches and existing
PRs. Fetch and rebase each feature branch onto its repository's current main.
Preserve milestone commits, resolve conflicts, and update the parent dependency
pin to the rebased Obsidian feature head. Record both baseline commits.

Run the isolated offline suite after resolving conflicts. Do not pool historical
results with a new source revision without identifying what changed. Existing
index acceptance results need repeating only if relevant backend/configuration
changes invalidate them.

**Exit:** clean working trees, valid dependency pin, no unexplained conflicts,
passing relevant regression tests. Commit any required integration fixes
separately. Rewriting a published branch later requires explicit publication
approval and a lease-protected push.

## M2 — Prepare evidence from deterministic findings

Replace undifferentiated note excerpts for supported maintenance candidates with
a versioned evidence contract. Include feature, source revision, line/span,
evidence identifier, deterministic classification, limitations, and allowed
proposal types. Keep this bounded and feature-specific.

- Link repair candidates come from parsed live references and eligible targets;
  fenced examples cannot become suggested replacement targets.
- Task candidates include all relevant mirrored occurrences and their statuses.
  An unresolved conflict supplies no authoritative completion state.
- Table candidates include parsed operands, declared units/currency, computed
  expected value and authored value. Arithmetic remains code-owned.
- Attachment candidates distinguish confirmed references, unknown ownership and
  excluded/plugin coverage. Unscanned references must never become absent ones.
- Claims and graph candidates preserve condition/scope differences and distinguish
  navigation edges from asserted dependencies.

**Validation:** deterministic tests for code/comment examples, conflicting task
copies, the 17.50 EUR total, unknown attachment ownership, unit equivalence,
navigation-only links, changed revisions and scope boundaries. Include clean
controls and deliberately wrong fake-model responses.

**Exit:** supported facts and permitted proposals are explicit; candidate
preparation cannot silently discard contrary evidence or infer permissions.

## M3 — Constrain interpretation and attach citations in code

Have Qwen select supplied evidence identifiers instead of reproducing quotations.
Validate identifiers against the candidate, then attach the exact source text,
location and revision in code. Reject unknown, stale or cross-candidate IDs.
Do not require legacy response compatibility unless a caller actually uses it.

Validate structured proposal fields against deterministic facts. Render factual
conflict/calculation statements from code-owned values. A model cannot approve
completion from conflicting task copies or replace a computed total with the
authored incorrect total. Keep explanations explicitly advisory; citation and
schema validation cannot prove arbitrary prose is semantically correct.

Preserve bounded budgets, pinned model identity, measured usage, cancellation,
no automatic retries and source revalidation. Failures must leave deterministic
reports available. No authored-note mutations or new default model tools.

**Validation:** fake-provider tests for invented IDs, irrelevant citations,
unsupported proposal types, conflicting numerical/status fields, stale evidence,
prompt injection, unavailable inference, budget exhaustion and cancellation.

**Exit:** all supported structural constraints pass; the known failure cases are
retained as regressions. The default remains model-free. If advisory quality still
fails, label it experimental rather than weakening validators or claiming success.

## M4 — Evaluate quality and cost on fresh controls

Freeze evidence schema, source commits, fixture hashes, configuration and rubrics
before inference. Retain the current failed cases as development regressions and
create a separately versioned set with new wording, names, values and conflicts.
Do not tune on that set after inspecting its results; subsequent tuning needs a
new evaluation set. Assistant-authored labels are not independent human labels.

Run the affected feature cases and fresh controls five times with Qwen 3.5 4B.
Compare code-only reports against code plus advisory interpretation on the same
inputs. Start with thinking off; a thinking-on comparison is optional if failures
remain and must record its additional tokens and latency. Use Nomic v2 only where
the tested path actually consumes embeddings; do not imply WP07/WP10 semantic
features exist merely because WP11 retrieval passes.

Grade separately: decision category, exact evidence attachment, factual meaning,
contradictory-evidence handling, action restrictions, abstentions and useful
coverage. Report each critical case individually. Include machine wall time,
time to an accepted result, input/output tokens, calls, first-load versus warm
timings and all raw failures. Temperature-zero repetitions measure stability,
not independent examples. Human review time remains unmeasured without a pilot.

**Exit:** zero false authoritative completion/total/permission conclusions in
critical controls; exact evidence and unchanged source bytes pass. Any remaining
quality failure is explicit and keeps that advisory capability experimental.
Store protocol, raw results and scope limits in `nuru/evals/vault_steward/`.

## M5 — Finish index faults and worker contention

Exercise actual conditions rather than naming a fault that a harness did not
produce:

- Hold a SQLite write lock from a separate connection/process; check bounded
  failure, observable status, release and successful reconciliation.
- Corrupt/truncate a disposable index; fail visibly and rebuild explicitly rather
  than overwriting an unrelated database or serving apparently healthy results.
- Interrupt and restart an active worker, inject embedding failure, and edit a
  note during indexing. Check persisted pending work, revisions and no partial
  note publication.
- Compare interactive queries with the worker off/on under matched vaults and
  pinned settings. Check yield/cancellation, no-change zero embedding calls,
  CPU/RSS, queue age, query p50/p95 and repeated-run variation.

Use fake inference for deterministic fault cases; use authorized local Nomic v2
and Qwen only where live contention is necessary. Define the workload and resource
envelope before timing. The existing proposed p95 target is no more than a 10%
increase; report absolute latency and variability, and mark failure honestly.

**Exit:** fault behavior is recoverable and observable, source bytes stay intact,
and the declared contention gate passes or the limitation prevents background
rollout under that workload. Publish reproduction details and results.

## M6 — Final local review and documentation

Review the cumulative diff in both repositories, emphasizing default-off behavior,
read-only contracts, SQLite ownership, symlinks, exclusions, cancellation and
dependency compatibility. Update tool/configuration contracts, examples,
milestone status and known limitations. Keep planned efficacy separate from
measured results and retain prior failed artifacts.

Run the full isolated regression suite, lockfile checks, CLI/MCP smoke checks,
whitespace checks and relevant new tests. Verify fixture/copy/source hashes.
Ensure fresh-clone reproduction does not depend on ignored local vaults or indexes:
the tracked generators must reconstruct required synthetic inputs, while the
benchmark-copy lane must state that the external DummyVault source is required.

**Exit:** reproducible local checks pass; no secrets or local vault/database dumps
are staged; release claims match delivered scope. Independent human labels and
a consenting personal-vault pilot remain follow-up acceptance work.

## M7 — Publish two linked PRs

This is the final external step. Obtain the outstanding approval to push both
branches, trigger GitHub CI and create PRs. Do not infer publication or merge
permission from local-model authorization.

1. Publish the Obsidian backend PR targeting its main branch. Include supported
   behavior, validation, failure handling and remaining experimental scope.
2. Publish the Nuru integration PR targeting main, linked to the backend PR.
   Keep the dependency pin reviewable and ensure private dependency checkout
   succeeds in CI.
3. Resolve CI/review issues in milestone commits and rerun affected checks. Do not
   merge either PR without explicit authorization. After the backend is merged,
   repin Nuru to the actual backend main commit, then rerun integration checks.

**PR-ready:** both descriptions are accurate, required CI passes, blocking review
findings are addressed, dependency ordering is clear, and remaining optional work
is explicitly deferred. Ready for review or merging does not itself authorize
merging.

## Authorization and outstanding inputs

Current local authorization covers new test vaults, the DummyVault copy, Nomic v2
embeddings and Qwen 3.5 4B validation. No additional model/provider is needed for
this plan. Do not start paid services, scan a personal production vault or enable
background inference under that authorization.

Publication approval is outstanding. Independently human-labeled semantic
acceptance and review-time efficacy require reviewer participation; they are not
prerequisites for an accurately scoped experimental first release, but remain
prerequisites for claiming those outcomes.
