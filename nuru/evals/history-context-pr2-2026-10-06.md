# PR 2: history sizes and context budgets

Branch: `feat/history-context-budgets`, from `plan-executor` at `b532930`.

## Baseline comparability correction

**The live numbers in the historical observations section were collected before the baseline environment configs
were copied to NuruCodex. They used v1.5 embeddings and had no `run_python`. They
are not comparable with the current v2/Python-enabled baselines. Re-run them with
matched configurations before making any baseline comparison.** This applies to
scores, timings, tool counts and context behavior; the old raw records are retained
as historical smoke evidence.

The current `.env.obsidian` selects `nomic-embed-text-v2-moe`. Both interactive
checkouts share `dummyVault/.obsidian-mcp-semantic-v2.sqlite3`. Suite runs override
the index path with their own batch-local file and build it from scratch; copying
fixtures now excludes interactive index/lock files and SQLite sidecars.

`.env.python` is now configured, but actual `run_python` availability still requires
a working Docker server/image at startup. `.env.deep-think` was not copied. Keep
the expert off; Sonnet/Haiku planning requires its credentials to be configured
explicitly. No credentials were copied or committed during this update.

The manifest now resolves the effective embedding model through the server's own
configuration loader, including defaults and process overrides. Previously it
recorded `null` for an unset variable even when v1.5 was used. Explicit embedding
settings are forwarded consistently to the scratch index builder and MCP server.

## Rerun with copied baseline configuration

Batch: `evals/decision_layer/.runs/suite-pr2-v2-python-memory-20261006-093147`.
Runs were scheduled after unit checks and after the user confirmed the CPU was
available. Effective settings:

- `granite4.2:3b`, default thinking `low`, 16,384-token context, isolated runtime
  tag `nuru-pr2-eval`; Ollama 0.35.1.
- `nomic-embed-text-v2-moe`; the batch-local index metadata confirms that model,
  1,500-character chunks and 768 dimensions.
- Python sandbox available, image
  `sha256:56357ac130fd71a8a09d8c9d72feedc215c8adbfc1de59680f9e256b8337ab14`.
  Both Session startup logs include `python-mcp`.
- Expert planner off; no expert calls and no credentials copied.

| Case | Turn scores | Every check passed | Turn time | Vault changes |
| --- | --- | --- | --- | --- |
| 35, ordinary | 5/5, 5/5, 5/5 | Yes | 27.1 s | None |
| 36, fixed answer-only plan | 5/5, 5/5, 5/5 | Yes | 5.7 s | None |

The planned reply `Codeword: Juniper` exposed another rubric formatting omission.
The saved transcript was explicitly re-scored with the corrected label-aware
rubric, SHA-256
`0c94702fb990229d7636849189d66c9f56aca6ddfed4aa60b23c79c330c615e9`.
Both cases pass every check; the raw collection records remain unchanged. A unit
regression covers that label form, and no additional model calls were used to
correct scoring.

Both memory cases made zero tool calls. These are memory/context smoke checks
with Python available in the contract; they do not exercise Python computation
or semantic retrieval. They do not constitute a baseline/candidate comparison.
The older Qwen/v1.5 timings are not used to claim an improvement.

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
  timestamp, current input, history and evidence, with 20% reserved for output, capped at 2,048 tokens.
  Complete old exchanges are evicted first, then the largest tool-result bodies
  are replaced with explicit omission notices until the request fits, without
  splitting calls/results. Only requests still too large after optional evidence
  is removed stop explicitly. The exact model-build SYSTEM rendering is cached
  at session startup.
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

Workspace suite: **629 passed**. Focused coverage includes the 100-content-token /
6,000-usage-token history regression under a 5,000-token budget, legacy migration
and rollback, orphan/empty exchanges, admission after a changed effective window,
SYSTEM/schema overhead, continuation eviction, oversized evidence, and planned
follow-up context. Driver tests verify one Session per sequence in both execution
modes; planner tests also cover prior context and an independently configured
planner’s effective window. Every Session unit test now uses temporary conversation databases and logs;
previously that isolation was local to `test_session.py`.

## Historical live observations (v1.5, no Python)

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
The default-window ordinary-mode repeat ran after the user confirmed resources
were available. Model runs are scheduled after implementation and coordinated
when CPU/GPU resources are already occupied.

The initial 32,768-token planned probe answered all three deployment-themed turns
correctly, without mutations, but took 355.1 seconds. None of these runs called an
expert planner. These are limited smoke checks; broader behavioral acceptance remains separate.

### Default-window verification and explicit re-scoring

`evals/decision_layer/.runs/suite-pr2-default-memory-20261006-091526`, same model
and sampling, effective window **32,768**: ordinary case 35 answered all three
turns correctly, with scores **5/5, 5/5, 5/5**, in **42.6 seconds**, with no vault
changes. Its last answer was `The codeword you gave me is "Juniper."`.

The collection-time regex incorrectly rejected that quoted answer. The final
rubric accepts quotation marks and Markdown around an affirmative answer, and
still rejects merely mentioning the word in a failed search report. Scorer
regressions cover formatting and explicit re-scoring. Raw runtime records remain
unchanged: `load_results(batch, cases_path=Path(".../cases.json"))` applies an
explicit revised rubric, requires exactly the same recorded prompts, and records
its SHA-256 on the returned results. Default scoring still uses the collected
rubric for reproducibility.

Using the rubric hash recorded for that historical re-scoring,
`e41a1b6596586eda259de952c310ba31efcde0dbd4c003c423f1adf7528bcd41`, re-scoring
confirms default-window case 35 and tight-window planned case 36 both pass every
check. Tight-window ordinary case 35 remains failed: no rubric change masks its
empty reply/context failure. No additional model calls were needed for re-scoring.

## Limits

UTF-8 bytes / four is a conservative improvement over raw English character
counts, not a tokenizer guarantee. Executor input clipping remains explicit in
its prompts. These small memory runs are smoke checks, not the full baseline /
candidate 34-case acceptance comparison planned for later hardening work. Tool
failure normalization and independent mutation authorization remain PR 3 and PR 4.


## PR review follow-up: context-pressure regression

Review fixes are in commit `4dfac47`. Continuations now evict complete prior
exchanges first, then replace the largest tool-result bodies with explicit
`[left out: ~N tokens; evidence unavailable ...]` notices until the estimated
request fits. Tool-call relationships and the step-limit final-answer instruction
stay intact. Original messages are retained for logging and server-overflow
recovery. The response reserve is `min(20% of window, 2048)`; at 16,384 the
estimated input allowance is 14,336 before fixed request costs. A request still
too large after removable content is omitted stops explicitly. Reduction events
record evicted history counts and omitted evidence sizes without logging its text.

`create_model()` returns its exact rendering, and Session caches that rendering's
SYSTEM prompt at startup. It no longer reads templates for budget calculations.
The suite defaults to `--tests 1-34` (/170); memory cases 35/36 remain opt-in and
`--tests all` remains explicit. Documentation reflects these changes.

Validation: **639 unit tests passed**. New checks cover capped reserves, largest
and multiple tool-result omissions, preserved call relationships and original
messages, irreducible requests, exact startup prompt caching, the default
34-prompt selection, and the step-limit final answer under context pressure.
Both GitHub CI checks passed on the implementation commit.

The authorized live regression was run after code, documentation and unit checks,
when CPU activity was low and Ollama had no models loaded:

```sh
MODEL=nuru-pr2-qwen-review NURU_BASE_MODEL=qwen3.5:4b \
NURU_PLANNER_MODEL=nuru-pr2-qwen-review NURU_MAX_NUM_CTX=16384 \
caffeinate -i .venv/bin/python nuru/evals/decision_layer/run_suite.py \
  --tests 25,31,32 --tiers default --repeats 5 \
  --label pr2-review-qwen4b --timeout 600
```

Batch: `evals/decision_layer/.runs/suite-pr2-review-qwen4b-20261006-095407`,
implementation commit `4dfac47`, clean checkout at collection. Qwen3.5:4b Q4_K_M,
default thinking off, Ollama 0.35.1, context 16,384. Base model digest:
`2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`.
Embedding index metadata confirms `nomic-embed-text-v2-moe`, 768 dimensions,
1,500-character chunks. Python image:
`sha256:56357ac130fd71a8a09d8c9d72feedc215c8adbfc1de59680f9e256b8337ab14`;
all 15 session startup logs confirm Python availability. No expert calls occurred.
Each case used a fresh scratch vault and the batch's own index.

Scores use the unchanged single-prompt rubrics from the current scorer, applied
also to the earlier raw records in the other checkout:

| Test | Five candidate scores | Candidate | after-qwen4b | tweak-qwen4b | Largest measured prompt |
| --- | --- | --- | --- | --- | --- |
| 25, status | 5, 5, 3, 4, 5 | 22/25 | 19/25 | 19/25 | 13,459 |
| 31, standup | 2, 2, 5, 5, 5 | 19/25 | 16/25 | 12/25 | 12,547 |
| 32, audit | 3, 3, 4, 3, 4 | 17/25 | 17/25 | not collected | 15,228 |

Earlier batches: `suite-after-qwen4b-20261005-194106` and
`suite-tweak-qwen4b-20261005-211544`. Model digest, context, sampling, declared
tools, submodule revisions, embedding model, Python image and semantic decision
configuration match. Raw decision-config hashes differ because each batch has
its own database path. The candidate vault hash
`d50237d06a8f93ea788e05e78fda3893f981bdbd3d5362c0380df490ee891321`
matches the tweak batch; the older after batch used
`45ded25aaf93116b59b9794739af36242d9a25fb14344eebbb9ad05f1b89e068`.
Earlier manifests lack rendered-prompt hashes. The current other checkout's
rendered Qwen Modelfile matches the candidate exactly:
`45de03832c3da004307023cb5cab5fbfdcbc495db3d645ced9f7d3557cd5d8da`.
These are historical comparisons, not a paired same-day experiment.

All 15 turns completed: no context-budget failures, crashes or timeouts. Four
audits reached the ten-step final-answer path; repetition 3 answered within nine
tool rounds. No live context-reduction events occurred; the omission
path is demonstrated by focused unit tests, not by these live transcripts.
Measured prompt usage is not the byte-based estimate used by the guard.
The candidate totals match or exceed the cited scores, but five stochastic runs
per case do not establish statistical improvement. Status and standup still have
content misses; audits still miss periodic-attach and/or report-rate conclusions.
The first-action-search prompt rule remains a separate change. PR #3 remains draft.
