# PR 2: history sizes and context budgets

Branch: `feat/history-context-budgets`, from `plan-executor` at `b532930`.

## Implementation

- `content_tokens` estimates saved content independently of actual `tokens_used`.
  The CLI still reads measured prompt usage; storage never imports Session configuration.
- Migration 1 backfills old rows in batches of 256 within one atomic transaction.
  IDs, roles, order, content and usage are preserved. Reopening is idempotent;
  interruption rolls back both schema changes and partial backfill.
- History scans at most 200 metadata rows in stable ID order and selects whole
  adjacent user/reply exchanges. Orphan rows are skipped, empty cancelled replies
  are retained, and zero/negative budgets never force a message into a request.
- Each model request includes estimated SYSTEM, tool/format schema, framing,
  timestamp, current input, history and evidence, with 20% reserved for output.
  Complete old exchanges can be evicted on continuation without splitting tool
  calls/results. Required evidence that cannot fit stops the turn explicitly.
  The existing single server-overflow recovery remains a fallback.
- Planner and executor calls receive bounded complete prior exchanges. Explicit
  executor/planner SYSTEM messages replace the baked assistant prompt for budgeting.
  Draft/answer instructions now permit the supplied conversation context.
- Sequence cases share one Session, vault and index for all turns. The runner
  restores scratch fixtures between cases, discovers cases beyond the legacy 34,
  records per-turn usage/diffs, and scores each turn. Acceptance requires every
  check, independent of the legacy average pass threshold. Case and scorer hashes
  are recorded in batch manifests.

## Verification

Workspace suite: **620 passed**. Focused coverage includes the 100-content-token /
6,000-usage-token history regression under a 5,000-token budget, legacy migration
and rollback, orphan/empty exchanges, admission after a changed effective window,
SYSTEM/schema overhead, continuation eviction, oversized evidence, and planned
follow-up context. Driver tests verify one Session per sequence in both execution
modes; planner tests also cover prior context and an independently configured
planner’s effective window. Every Session unit test now uses temporary conversation databases and logs;
previously that isolation was local to `test_session.py`.

## Live observations

Local evaluations use the isolated `nuru-pr2-eval` tag, Qwen3.5 4B, thinking off,
scratch vault/index copies, and no expert planner. Runtime results remain in the
ignored `evals/decision_layer/.runs/` directory.

An initial deployment-themed probe completed all three turns without mutations.
Turn two correctly answered “5 weeks” from the earlier 35-day answer, including a
`calculate(35 / 7)` call. Turn three recalled Juniper in a search query but then
claimed no matching note, so manual review rejected that answer. Its original
positive-word-only rubric falsely counted the mention; the committed rubric now
requires an affirmative statement of the earlier codeword or a direct codeword answer. The assistant's existing FIRST ACTION instruction
sends project questions to vault search. The committed fixtures explicitly introduce
a conversation-only codeword to test conversational memory without that ambiguity;
the original probe is retained as a documented prompt limitation.

The first live attempt also exposed an integer/mapping mismatch in the new driver’s
failure counter. That was fixed and covered with a driver-level regression.

### Final recorded acceptance batch

`evals/decision_layer/.runs/suite-pr2-acceptance-20261006-091126`,
Ollama 0.35.1, `qwen3.5:4b` Q4_K_M (digest
`2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`),
thinking off, effective window **8,192**:

| Case | Turn scores | Every check passed | Turn time | Vault changes |
| --- | --- | --- | --- | --- |
| 35, ordinary | 5/5, 5/5, 2/5 | No | 42.0 s | None |
| 36, fixed answer-only plan | 5/5, 5/5, 5/5 | Yes | 6.9 s | None |

Ordinary turn three searched for `codeword Juniper duration 35 days` even though
the fact was in history. Its result exceeded the remaining estimated budget, so
the harness stopped before another model request and preserved the turn with an
explicit context limitation. The aggregate score is 4/5, but `acceptance_passed`
is **false**; the average does not hide the failure. An earlier 8,192-token run
answered all six turns correctly on manual review, illustrating model variability.
A default-window ordinary-mode repeat remains pending resource availability; the
user requested that model runs be scheduled after implementation and coordinated
when CPU/GPU resources are already occupied.

The initial 32,768-token planned probe answered all three deployment-themed turns
correctly, without mutations, but took 355.1 seconds. None of these runs called an
expert planner. A draft PR is appropriate while ordinary-mode live acceptance
remains incomplete.

## Limits

UTF-8 bytes / four is a conservative improvement over raw English character
counts, not a tokenizer guarantee. Executor input clipping remains explicit in
its prompts. These small memory runs are smoke checks, not the full baseline /
candidate 34-case acceptance comparison planned for later hardening work. Tool
failure normalization and independent mutation authorization remain PR 3 and PR 4.
