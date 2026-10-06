# Validated dispatch: paired regression (2026-10-06)

**Assessment: keep PR #5 draft.** All 70 requested runs completed. After the
explicit test-32 manual correction below, baseline passes **26/35** with
**136/175** points; candidate passes **24/35** with **127/175** points.
Candidate median turn latency is **51.3 s**, versus **43.0 s** baseline
(+19.3%); p95 is **279.8 s** versus **250.9 s** (+11.5%).
The observed standup and sensor-fit shortfalls need assessment before merging.
These five unseeded repeats per case do not establish a statistically significant
or causal regression, but they do not support a no-regression acceptance claim.


## Paired regression after PR #4 merge (2026-10-06)

PR #4 merged into `plan-executor` at `a0c413e`. PR #5 was rebased cleanly
and its candidate evaluated at `3879304`. The baseline was already running
at `535517b`; the intervening PR #4 changes only scoring, reference checks,
tests and documentation, with no changes to model execution or tool behavior.
Its runs remain a valid pre-dispatch baseline. Both arms are graded with the
merged PR #4 scorer, SHA-256
`6c51de7425d0cab73b09e696fbad115a80162a788d9cbba35aa85ed4de412c0d`.
The baseline's original manifest records its collection-time scorer separately.

Cases **30, 31, 9, 7, 8, 12, 32**, five independent repeats each per arm;
no planning or expert calls. Qwen3.5:4b, Q4_K_M, thinking off, 16,384 context,
nomic-embed-text-v2-moe and Python enabled. Both use a frozen copy of dummyVault
and a separately built scratch semantic index. The runs are sequential, baseline
then candidate; repeats are matched by case/repeat index without fixed RNG seeds.
This is a targeted regression comparison, not the full 34-case suite.

Fixture SHA-256: `d50237d06a8f93ea788e05e78fda3893f981bdbd3d5362c0380df490ee891321`.
Rendered Modelfile SHA-256:
`45de03832c3da004307023cb5cab5fbfdcbc495db3d645ced9f7d3557cd5d8da`.
Qwen digest: `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`.
Python sandbox image:
`sha256:56357ac130fd71a8a09d8c9d72feedc215c8adbfc1de59680f9e256b8337ab14`.

The baseline manifest's dirty flag comes from symlinks to the identical Rust
build artifacts in the primary checkout, not tracked source edits: its tracked
diff is empty (SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`).
Math/Python submodule commits match across arms; Obsidian differs intentionally
because the candidate adds structured refusal receipts.

After rebase: **714 tests passed** with
`VAULT_PATH=/tmp/ci-vault OBSIDIAN_SEMANTIC_DB='' .venv/bin/python -m pytest -q --disable-warnings`.

## Scores and pass rates

The merged scorer marks >=4/5 as a pass. Arrays are repeat indices 0 through 4.
Candidate test 32 is shown after removing the explicitly reviewed false-positive
verdict point; all other entries retain the merged scorer's results.

| Case | Behavior | Baseline scores | Candidate scores | Baseline passes | Candidate passes |
|---|---|---|---|---|---|
| 30 | Correction filing | 4, 4, 4, 4, 5 | 4, 4, 4, 5, 4 | 5/5 | 5/5 |
| 31 | Standup transcript | 5, 5, 5, 2, 1 | 2, 1, 5, 2, 5 | 3/5 | 2/5 |
| 9 | Sensor fit | 4, 1, 4, 4, 5 | 5, 5, 4, 0, 0 | 4/5 | 3/5 |
| 7 | Power budget | 5, 4, 5, 5, 5 | 5, 5, 5, 4, 4 | 5/5 | 5/5 |
| 8 | PDP-drop statistics | 4, 4, 5, 4, 3 | 4, 4, 4, 3, 4 | 4/5 | 4/5 |
| 12 | Data plan / flash wear | 4, 4, 4, 4, 5 | 4, 4, 4, 4, 4 | 5/5 | 5/5 |
| 32 | Battery / PSM / NAT audit | 3, 2, 3, 3, 3 | 3, 3, 3, 3, 3 | 0/5 | 0/5 |

Raw automatic totals are baseline **136/175, 26/35 passing** and candidate
**128/175, 25/35 passing**. Manual correction makes candidate **127/175,
24/35 passing**. The per-run CSV preserves both scores.

### Test 32 scorer limitation

All ten battery-audit replies were reviewed for the architectural verdict and
biggest-risk assertion. Candidate repeat **2**, session **2273a3a2**, incorrectly
gets the verdict point from the merged scorer. It calls scenario B a pass early
in the answer, then concludes **“No, not yet”**, says **“You must fix PSM
engagement”**, and calls PSM the biggest risk. Its conclusion conflates modem-off
periodic attach with PSM. Modem-off architecture B does not require PSM;
report rate or alarm latency is the relevant remaining risk.

The regex misses the comma in the overall denial and the PSM dependency expressed
across subsequent sentences. This is a false-positive **4/5**, manually **3/5**.
The other nine replies receive no verdict point and likewise fail that
architectural check. No audit passes after this correction. The pinned scorer
was not silently changed or refitted to these runs; its hash and raw results
are preserved. A scorer fix and fixture for this whole reply remain follow-up
work. Neither the dispatch PR nor the scorer solves Qwen's architectural reasoning.

## Latency and execution evidence

| Case | Baseline median turn (s) | Candidate median turn (s) |
|---|---:|---:|
| 30 | 32.9 | 42.8 |
| 31 | 43.1 | 68.7 |
| 9 | 67.6 | 159.9 |
| 7 | 43.0 | 49.2 |
| 8 | 38.6 | 38.2 |
| 12 | 28.7 | 32.3 |
| 32 | 238.3 | 263.4 |

Turn latency excludes process startup; per-case wall medians including startup
are **54.4 s** baseline and **63.4 s** candidate. Total recorded case wall time
is **3,136.6 s** baseline and **3,794.3 s** candidate (excludes initial scratch-index
preparation). All repeats are retained, including the first model-loading run
and long read/retry outliers. p95 uses nearest rank across 35 turn durations.
Different stochastic tool paths and sequential run order limit causal attribution.

| Metric | Baseline | Candidate |
|---|---:|---:|
| Tool calls | 177 | 182 |
| Model calls | 211 | 215 |
| Prompt tokens summed across calls | 1,861,700 | 1,904,499 |
| Completion tokens | 26,897 | 23,446 |
| Failed tool calls | 13 | 11 |
| Process crashes / timeouts | 0 / 0 | 0 / 0 |
| Explicit turn failure records | 0 | 2 |
| Changed files in cases 7, 8, 9, 12, 32 | 0 | 0 |

- **Corrections:** 5/5 pass in both arms. Each arm totals 21/25 points; four
  repeats miss the second correction target. Schema/receipt protection does not
  establish canonical target provenance.
- **Standups:** baseline 3/5 versus candidate 2/5. Baseline repeat 3 created a
  note with the wrong metadata; repeat 4 replaced an existing transcript and
  produced a trash backup. Candidate repeats 0 and 3 searched old notes and
  answered without creating a transcript; repeat 1 appended to an existing
  transcript. Candidate successful repeats still invent participants not supplied
  in the prompt; the five-check rubric does not assess this content fidelity.
  There were no tool failures in these candidate standup cases.
- **Sensor fit:** baseline 4/5 versus candidate 3/5. Baseline repeat 1 made
  eight failing Python calls and stopped at the duplicate-call guard after
  **391.2 s**, scoring 1/5 despite `failure=null`. Its log truncates the error
  before the cause. Candidate repeats 3 and 4 stop after the initial Python
  failure plus exactly two failed corrections, at **159.9 s** and **172.0 s**,
  scoring 0/5 with explicit correction-exhaustion failures. Full tracebacks
  identify list-minus-float and float-not-subscriptable `TypeError` failures. Candidate repeat 2 scores 4/5
  after one failed Python call and a tool-free answer, without a successful
  replacement computation; do not count it as verified tool recovery.
- **Statistics recovery:** candidate repeat 1 successfully corrects unsupported
  `**` to explicit multiplication and unsupported `round(...)` to a supported
  expression. Each failed calculation is followed by a successful call. Repeat 4
  also completes after two `INVALID_SYNTAX` failures. Across the arm, errors are
  seven Python `SCRIPT_ERROR`, three math `INVALID_SYNTAX`, and one math
  `UNKNOWN_IDENTIFIER`. Baseline errors are eight Python, two math and three
  write refusals, without normalized error codes.
- **Writes:** candidate journals contain exactly **8 completed** attempts and
  **0 in_flight / uncertain / not_completed** attempts. No candidate mutation
  failed, so these model runs do not exercise uncertain-write blocking or
  rejected-write recovery. Deterministic fault tests and the real MCP contract
  probes remain the evidence for those paths. Both arms leave all five numerical/
  audit case vaults unchanged. A completed receipt certifies execution, not that
  the model selected the correct action, target or content.

## Artifacts and reproduction

Raw artifacts are ignored under `nuru/evals/decision_layer/.runs/`:

- `suite-pr3-paired-baseline-20261006-125041`: results, manifest and session logs. Results SHA-256 `61ba30eda6a42cdb73528e81ec39112ecf6e507c326d754dba56fab83bc6e213`.
- `suite-pr3-paired-candidate-20261006-134324`: results, manifest and session logs. Results SHA-256 `43adb29a6d2c8975b74c8d1c1a6cadfa34dea932966b2d716175d407d524415a`.

Baseline results, manifest, config and logs were also copied into the primary
checkout's ignored `.runs` folder; the attached baseline worktree retains its
original history and indexes. Candidate retains per-case SQLite histories.
The collection manifest's original baseline scorer hash differs intentionally
from the common merged scorer used for this report.

Commands (run in the corresponding checkout, with the same frozen fixture):

```sh
# Baseline: 535517b, original plan-executor runtime; candidate: 3879304, rebased PR #5.
# ARM is baseline or candidate; run sequentially.
MODEL=nuru-pr3-paired-$ARM NURU_BASE_MODEL=qwen3.5:4b \
NURU_PLANNER_MODEL=nuru-pr3-paired-$ARM NURU_MAX_NUM_CTX=16384 \
NURU_EVAL_VAULT=/tmp/nuru-pr3-paired-fixture-20261006 \
caffeinate -i .venv/bin/python nuru/evals/decision_layer/run_suite.py \
  --tests 30,31,9,7,8,12,32 --tiers default --repeats 5 \
  --label pr3-paired-$ARM --timeout 600
```

The baseline uses its own locked virtual environment and original Obsidian
`3b3a14e`; candidate uses merged receipt dependency `3e084e3`.
Suite histories are per case on candidate and separate conversations in one
batch DB on baseline. Baseline has no mutation journal. No expert API key or
paid planner is required or used. Earlier v1.5/no-Python runs remain ineligible
for comparison.

The PR stays draft pending assessment of the standup/sensor outcomes, latency
increase, and the remaining scorer false positive. This report completes the
requested paired runs; it does not claim that the merge criteria passed.


## Recovery follow-up: test 9 × 5

The exhaustion abort and changing recovery tool list are fixed, and the Python
dependency now shows the failing source line without adapter frames. See the
[scoped rerun](validated-dispatch-test9-recovery-2026-10-06.md): 4,3,2,5,5
(19/25; 3/5 passing), median 43.7 s, no tool/turn failures or vault changes.
Only test 9 was repeated; the earlier seven-case aggregate remains historical.
