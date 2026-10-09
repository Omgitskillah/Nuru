# M4: constrained advisory evaluation

The protocol was frozen before generation in `readiness_advisory_eval.py` and the
raw result's fixture/source hashes. Reproduce from tracked inputs:

```sh
.venv/bin/python nuru/evals/vault_steward/readiness_advisory_eval.py --repeats 5 --output /tmp/readiness-advisory.json
```

No DummyVault or ignored local database is required. Both lanes are generated in
disposable directories: ten previous failure/development controls and ten fresh
controls with new names, values and conditions. Labels are assistant-authored,
not independent human labels. Fresh-v1 is now inspected; it must not be reused as
a held-out tuning target. No prompt/schema tuning or retries followed inspection.

Qwen `qwen3.5:4b`, digest `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`,
Ollama 0.35.1, raw ChatML, closed empty thinking block, temperature zero,
seeds 1–5, 16,384 context, 1,024 maximum output. The fully rendered UTF-8 byte
count plus two conservatively reserves input tokens; measured usage is checked.
Nomic is not used in this path. Inference was authorized locally by the user.

## Results and cost

100 calls: **95/100 structural constraints passed; 90/100 proposal-usefulness
controls passed**. All source bytes stayed unchanged; no budget violations or
retries occurred. All accepted citations were attached from original evidence by
code, including exact source lines and revisions. No model quotations were trusted.
No accepted structured proposal asserted false task completion, an incorrect
total, sharing permission, deletion eligibility, or dependency semantics.

| Measure | Result |
|---|---:|
| Code-only candidate preparation, median | 4.46 ms |
| Advisory generation wall time, median | 9,971.63 ms |
| Input / output tokens | 109,520 / 27,065 |
| First-call load time | 6,867.82 ms |
| Subsequent median load time | 2.91 ms |

Code preparation already exposes the facts required by these controls. Its timing
includes preparation after snapshot scanning; advisory timing excludes the same
scan. This is a matched component comparison, not whole-session latency or
human review-time efficacy. Raw results include each call's wall and load/eval
timings. Failed useful proposals have null time-to-accepted-result; no average
silently treats failed responses as accepted answers. Warm measurements are not
independent cold starts. Five zero-temperature repetitions measure stability,
not 100 independent examples.

## Separate prose inspection and critical cases

Assistant inspection of the raw suggestions gives 17/20 fully matching meanings,
two partial/weak explanations and one false explanation, repeated across five
runs: **85 full / 10 partial / 5 false**. These are raw-prose grades, including
the rejected link response; they are separate from validation/decision grades.
Independent human grading remains pending.

| Feature/control | Outcome over five repetitions |
|---|---|
| Owner missing (development) | Abstains; no invented owner |
| Broken link (development) | No invented target; fenced example absent from evidence |
| Referenced attachment (development) | Restricts deletion but weakly confuses wiki syntax with unknown plugin ownership: partial |
| Task mirrors (both lanes) | Reports open/completed conflict; no authoritative completion |
| EUR total (development) | Correctly says 17.50 vs 20.00, but chooses generic review; useful-proposal control fails |
| Missing next action (both lanes) | No invented action |
| Shared/private route (both lanes) | No routing authorization inferred |
| Exact copies (both lanes) | Comparison only, no merge/delete |
| Equivalent durations (development) | Code-normalized 2.5 s / 2500 ms; exact originals attached |
| Navigation edges (both lanes) | No asserted dependency |
| Owner missing (fresh) | Safe uncertainty, but irrelevant scope-property contrast: partial |
| Broken link (fresh) | Invents a second evidence ID; rejected 5/5; prose itself is cautious |
| Referenced attachment (fresh) | Invents older revision and different syntax history: false 5/5, despite valid IDs |
| EUR total (fresh) | Selects computed 11.55 vs authored 15.00 correction |
| Different conditions (fresh) | Preserves dry/wet qualification; no contradiction/truth selection |

Decision-category agreement alone would miss the false attachment history and
the generic table proposal. The new interface protects structured facts and
actions but cannot prove arbitrary prose. **Advisory quality acceptance remains
failed; experimental/default-off status is retained.** Thinking-on comparison was
optional and was not run: code already provides the authoritative values, and
these results do not justify paying the latency of advisory inference by default.

Raw evidence: [readiness-advisory-results-2026-10-09.json](readiness-advisory-results-2026-10-09.json).
Earlier failed artifacts remain historical and are not pooled with this run.
