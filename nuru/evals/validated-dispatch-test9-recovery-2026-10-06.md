# Test 9 after recovery and Python diagnostic fixes (2026-10-06)

The requested **test 9 × 5** rerun is complete. Scores: **4, 3, 2, 5, 5**,
**19/25**, **3/5 passing**. All five produced replies;
there were **no failed tool calls, explicit turn failures, crashes, timeouts or
vault changes**. Median turn latency is **43.7 s**.

Previous candidate test-9 scores were **5, 5, 4, 0, 0**: **14/25**, **3/5 passing**,
median **159.9 s**, with two correction-exhaustion aborts. The original baseline
was **4, 1, 4, 4, 5**: **18/25**, **4/5 passing**, median **67.6 s**.
The rerun improves recorded points and latency but leaves the pass rate at 3/5.
These independent, unseeded repeats do not isolate the contribution of each fix;
no errors naturally occurred in this new batch, so recovery behavior is verified
by deterministic tests rather than inferred from these successful executions.

## Changes evaluated

Nuru head **`c965e9103af10959e6b69200cdb444900fed8c02`** was clean at launch:

- Ordinary recovery retains the same advertised tools. Failed-evidence write
  eligibility is checked in code immediately before dispatch, including when the
  model requests a still-advertised write. Unrelated successful reads do not clear
  failed dependencies; uncertain writes still stop and remain journaled.
- Exhaustion requests a tool-free answer from successful evidence with unresolved
  failures explained, instead of aborting. Tier escalation and repeated-call
  detection cannot bypass that final answer. A model that requests tools in the
  final-answer phase is stopped without dispatch; uncertainty/internal-error
  stops are unchanged.
- Python dependency **`3da1447`**, [PR #1](https://github.com/Omgitskillah/python-4-llms/pull/1),
  now merged as `8114e7c` and repinned (identical tree to the tested `3da1447`),
  registers the in-memory `script.py` source and strips only input-adapter frames
  from uncaught tracebacks. Script/library frames, failing source lines, exception
  chains/groups, syntax carets, partial stdout and exit codes are preserved.
  A rebuilt immutable image is required; older images keep older diagnostics.

A final-answer retry guard was added after this batch: an empty thinking-only
answer retry also keeps tools disabled. Its deterministic test passes; all five
recorded model repeats returned normally without entering recovery, so this guard
does not alter their executed paths. The manifest head above remains unchanged.

A subsequent fake-model regression reproduced an endless reasoning-only loop
when `assess_effort` was enforced, both after exhausted recovery and at the step
limit. Final-answer calls now force `think=False` after tier selection. Even a
model that ignores that setting and returns only reasoning finishes through the
existing no-reply fallback without another model call. Both new cases failed
before the guard and pass with it; the focused dispatch/enforcement suite now
passes **86 tests**. No additional model evals were run; the five recorded repeats
did not enter either final-answer path.

## Per-repeat findings

| Repeat | Score | Turn (s) | Fit tool | Failure | Changed files |
|---|---:|---:|---|---|---:|
| 0 | 4/5 | 69.7 | Python | None | 0 |
| 1 | 3/5 | 30.5 | statistics | None | 0 |
| 2 | 2/5 | 28.0 | statistics | None | 0 |
| 3 | 5/5 | 43.7 | Python | None | 0 |
| 4 | 5/5 | 58.4 | Python | None | 0 |

Repeat 0 fits the correct data and compares with the eyeballed fit but misses the
200-ppm curvature check. Repeats 3 and 4 pass all five checks.

The remaining failures are **data selection/alignment**, not Python execution:

- Repeat 1 omits the measured `(0 ppm, 812 counts)` row. Its `statistics` inputs
  are x=`[10,25,50,100,200]`, y=`[1490,2510,4205,7588,14102]`. It reports
  slope 66.38 and intercept 867.92 rather than the full-data fit near 66.50 and
  850.53. It discusses residual curvature at 100 ppm; the existing loose regex
  credits any nonlinearity mention for the 200-ppm check. That rubric limitation
  is recorded, and the scorer remains unchanged for this comparison.
- Repeat 2 pairs those same five ADC values with x=`[0,10,25,50,100]`, shifting
  every concentration and dropping the 200-ppm position. The successful
  statistics call therefore produces the wrong fit (slope 127.54, intercept 1260).
  Successful execution does not establish correct data extraction.

All three repeats that used Python passed the rubric; the two using `statistics`
failed. Five runs are insufficient to infer a general tool-selection rule, and
no fit tool was forced. Repairing extraction/alignment is separate follow-up work;
these results do not justify marking the entire PR ready to merge.

## Verification and provenance

- Focused Nuru dispatch/enforcement tests: **84 passed**. Cases verify final answers
  after two failed corrections, unchanged recovery tool definitions, code-enforced
  dependent-write refusal, no dispatch in the final-answer phase, priority over tier
  escalation, and repeated-call recovery. Existing uncertainty tests still pass.
- Five focused Python adapter tests passed. The system-Python group case accounts
  for Python before 3.11; groups are also exercised in the actual pinned container.
- One real Docker/MCP diagnostic test passed, covering runtime, syntax and
  exception-group source lines without adapter frames on Python 3.14.8.
- Changed Rust test targets pass formatting and Clippy. Both Nuru CI jobs and
  Python dependency CI passed for the implementation commits.
- Only **test 9** was rerun through a model. None of the other six eval cases was
  repeated, and no planner, expert or paid reference API was used.

Matched with the earlier comparison: Qwen3.5:4b Q4_K_M, thinking off,
16,384 context, v2 embeddings, Python enabled, the same frozen dummyVault,
case prompt and merged PR #4 scorer. Both batches build independent scratch indexes.
The Python image intentionally differs to include the diagnostic fix. The local
ignored `.env.python` was updated to the newly built image; it is not committed.

New image: `sha256:8d2f9a52e1e63b85fb4d08bea4ddf200b0393edb31cffb20c09c4137254dd246`.

Fixture SHA-256: `d50237d06a8f93ea788e05e78fda3893f981bdbd3d5362c0380df490ee891321`.

Scorer SHA-256: `6c51de7425d0cab73b09e696fbad115a80162a788d9cbba35aa85ed4de412c0d`.

Raw ignored batch: `nuru/evals/decision_layer/.runs/suite-pr3-recovery-t9-20261006-163924`.

Results SHA-256: `f3c84cbed604222f998055dd3ef681e2903e569ac20222611d2d7a5e389ac7dc`.

```sh
MODEL=nuru-pr3-recovery-t9 NURU_BASE_MODEL=qwen3.5:4b \
NURU_PLANNER_MODEL=nuru-pr3-recovery-t9 NURU_MAX_NUM_CTX=16384 \
NURU_EVAL_VAULT=/tmp/nuru-pr3-paired-fixture-20261006 \
caffeinate -i .venv/bin/python nuru/evals/decision_layer/run_suite.py \
  --tests 9 --tiers default --repeats 5 --label pr3-recovery-t9 --timeout 600
```

The historical 70-run paired report remains intact. This scoped rerun supersedes
its description of recovery-exhaustion behavior and supplies new test-9 evidence;
it is not a fresh seven-case aggregate. Other observed issues and the scorer's
battery-audit false positive remain unresolved. The Python dependency merged as `8114e7c` and is repinned; its tree is
identical to the tested `3da1447`, so repinning requires no further model rerun.
