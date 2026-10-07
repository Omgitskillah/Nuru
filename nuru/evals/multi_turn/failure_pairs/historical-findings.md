# Historical failure selection (2026-10-07)

“Consistent failure” means failure in every repeat of an identified comparable
cohort, not every model/configuration ever tested. Separate true failures below
4/5 from repeated omissions in 4/5 passes. Historical artifacts were inspected
without generating new model responses.

## Most relevant recent evidence

The [October 6 paired CSV](../../validated-dispatch-paired-2026-10-06.csv) supplies
reviewed points, times and missed requirements for 70 runs. Both arms used Qwen
4B, thinking off, v2 embeddings and Python, but different implementations. Keep
arms separate; the later test-9 recovery run is a third cohort, not five extra
samples of the old candidate.

| Original test | Baseline: passes / full credit | Candidate: passes / full credit | Median seconds OS baseline / candidate | Selection |
|---|---|---|---|---|
| 32 battery audit | 0/5 / 0/5 | 0/5 / 0/5 | 238.3 / 263.4 | Consistent recent failure |
| 9 calibration | 4/5 / 1/5 | 3/5 / 2/5 | 67.6 / 159.9 | Recurrent, not universal failure |
| 31 transcript | 3/5 / 3/5 | 2/5 / 2/5 | 43.1 / 68.7 | Recurrent creation failure |
| 8 PDP statistics | 4/5 / 1/5 | 4/5 / 0/5 | 38.6 / 38.2 | Repeated SD/citation omissions |
| 12 Memfault | 5/5 / 1/5 | 5/5 / 0/5 | 28.7 / 32.3 | Crash-loop lifetime omitted in 9/10 |
| 30 correction | 5/5 / 1/5 | 5/5 / 1/5 | 32.9 / 42.8 | One affected target omitted in 8/10 |
| 7 power budget | 5/5 / 4/5 | 5/5 / 3/5 | 43.0 / 49.2 | Not a failure priority; omitted from pairs |

“Passes” here is the historical >=4/5 threshold; full credit is 5/5. These are
recorded scores, not regrading under the new stricter shared rubrics.

The [latest test-9 rerun](../../validated-dispatch-test9-recovery-2026-10-06.md)
scores **4, 3, 2, 5, 5**, hence 3/5 legacy passes and 2/5 full credit, median
43.7 s. Its remaining failures dropped the zero row or shifted ADC/ppm alignment;
all calls completed without the old Python abort. The new sequence targets input
alignment and residual checking, not a supposedly still-unfixed abort.

Test 32 has an additional scorer issue: an early affirmative can receive credit
in spite of a later “No, not yet” conclusion requiring PSM. The paired CSV already
records candidate repeat 2 as reviewed 3 rather than automatic 4. Use the reviewed
column, and manually adjudicate final conclusions in both new arms. Do not infer
that keyword checks alone establish the verdict.

## Older full-suite leads, not current failure claims

The [October 4 model comparison](../../qwen-vs-granite-check-2026-10-04.md) includes
34 tests × three repeats per model. Its Qwen means identify further candidates:

| Test | Qwen mean / 5 | Why retain a sequence |
|---|---|---|
| 6 broken links | 3.0 | Collect targets and resolve aliases before final sweep |
| 13 UART | 3.7 | Separate buffer capacity from clock-divider framing error |
| 20 contradictions | 2.7 | Gather both sides, then classify correction / chronology / scenario |
| 21 open actions | 1.7 | Sweep, deduplicate and check later resolutions |
| 25 manager message | 2.0 | Read directives and 1:1 before drafting |

Means do not recover exact per-repeat pass counts. These runs predate v2
embeddings, Python availability and later harness/prompt fixes; do not pool their
scores/timings with the October 6 cohorts. Refresh OS for each before deciding
whether MT solves a current defect. The older report mentions confidential-data
non-disclosure, but missing the confidential note is not proof of robust handling.

Test 17 originally scored 1.0/5. After v2 retrieval and current-date injection,
[Qwen 4B alone scored 5,5,5](../../live-planner-check-2026-10-05-embeddings.md),
median 25.2 s. It is now a **resolved control**, useful for detecting unnecessary
MT overhead, not evidence of a consistent current failure. Test 22 had Qwen mean
4.0/5 in the older full suite; no consistent current failure is established, so
it is not selected as a standalone failure pair.

The [PR2 observations](../../history-context-pr2-2026-10-06.md) explicitly warn
that early NuruCodex runs used v1.5 and no Python. They are excluded from baseline
claims. Historical paid-reference reviews are evidence already recorded; this
work makes no new expert/model calls.

## Requests additional turns cannot satisfy as written

### Test 1: total vault files including Templates

The old scorer expects 51 Markdown files and 40 non-template notes. The prompt
says “files”, which could also include indexes/settings. Current MCP working-set
rules hide templates and hidden folders from all discovery/read tools. Bounded
browse also lacks a general pagination contract. Python runs in a sandbox without
vault access. Neither arm can legitimately establish the excluded count through
the advertised tools; a memorized 51 would be unsupported.

This is an **unsupported scope plus an ambiguous denominator**, not evidence a
model cannot count. Expected diagnostic behavior: explain the accessible scope,
clarify the requested file definition, and state the unavailable total. Add a
scoped count/inventory tool and version the oracle before treating this as a
normal success comparison. Do not quietly give MT an extra filesystem tool.

### Test 27: battery lifetime into parsecs

The requested direct conversion is **dimensionally impossible**: time to length
requires additional assumptions such as velocity. However, the original rubric
correctly expects a refusal, so **the test itself is passable**. Pair concise
refusal accuracy/time as a separate control if desired. Do not label appropriate
pushback a failure or inject velocity to claim the original task was solved.

### Test 33: expert attachment and added periodic-task analysis

The current [model contract](../../../src/core/tool_contract.py) retires
`ask_an_expert`. A workflow fallback does not offer note-content attachments for
this requested review. Thus the exact expert action is unsupported today.

Separately, the proposed additional periodic task lacks WCET, period, deadline
and priority. Existing task utilization can be recomputed (0.36), and a conditional
bound derived, but an unconditional schedulability verdict for the added task
cannot be established. Blocking and task/deadline assumptions also matter.

Correct behavior explains the unavailable expert action and missing parameters;
no fabricated attachment or expert answer. If expert routing and task parameters
are deliberately supplied later, that is a new versioned capability lane. Never
compare its success against a baseline that lacks those capabilities.

## Limits of “can’t pass”

The supported sequences are hypotheses, not guaranteed repairs. For test 32,
the vault supports a conditional on-paper answer, but cannot demonstrate actual
three-year hardware life or settle unspecified product requirements. Correctly
qualified uncertainty is a pass; demanding a proven unconditional guarantee
would make that interpretation unanswerable.

Vault-wide sweeps (6, 20, 21) can be limited by truncation and the task budget.
Extra turns may extend retrieval opportunities, but both arms retain the same
600-second total budget. If complete coverage cannot be established, report the
limit; do not assert that more conversation created missing evidence.
