# Test 32: is the audit answerable, and a scorer that judges the verdict (2026-10-06)

Test 32 asks whether the 3-year battery requirement can still be met across the battery model, PSM decoding
and NAT keepalive notes. The battery note's own answer: architecture B, *periodic attach, modem fully off
between reports*, gives about 13.6 years. It doesn't need PSM: "met with margin only if the device uses
periodic attach **or** PSM truly works". The risks the note names are the report rate (6 an hour gives
2.3 years) and an alarm-latency requirement that could force always-connected ("the real tension").

## 1. Reference check: Claude Sonnet 5.5 with the same notes

[reference_check_32.py](decision_layer/reference_check_32.py) gives Sonnet 5.5 (adaptive thinking, no
tools, no Nuru) test 32's prompt and the seven notes Qwen read across its runs, including the superseded
battery model. It uses dummyVault data only and no local model.

| Run | Verdict | Biggest risk named |
|---|---|---|
| 1 (64k output) | Yes, only via periodic attach at ~1 report/hour; always-connected fails | Alarm-latency requirement |
| 2 | Met on paper only by B (~4x margin); not demonstrated | Unmeasured standby current |
| 3 | Only in B, at a modest report rate, with no tight alarm latency | Alarm-latency requirement |

All 3 runs (and 3 earlier runs capped at 16k output, two of them cut short) keep periodic attach
separate from PSM. **The test is answerable from the notes Nuru reads**, so Nuru's "No, PSM isn't
engaging" is a reasoning failure, not an unclear test. Batches: `.runs/reference-32-20261006-111155.json`
and `-111858.json`.

Every local setup tried gets the verdict wrong. Qwen 4B alone 0/5, Sonnet -> 4B 0/5, Qwen 9B alone 0/3,
Sonnet -> 9B 0/3.

## 2. A verdict-aware scorer

The old check for "periodic attach meets it" matched words in a sentence, with a short list of negations.
It credited "only met in the theoretical periodic attach scenario, which is currently unachievable", and
it failed all three of Sonnet's correct, hedged answers.

`score_32` now has:
- **periodic attach meets it, without PSM.** Some sentence says B meets the requirement (hedged answers
  count, e.g. "only with periodic attach", "yes on paper", "a theoretical estimate pending validation").
  No sentence denies it ("unachievable", "invalid", "does not meet", "fails to meet"...) or makes B depend on PSM ("relies on PSM", "only works if PSM", "with PSM
  working", a "Yes, if:" list with a PSM condition). And there is no overall "no" about the requirement;
  a "no" about always-connected alone is right.
- **biggest risk: report rate or alarm latency.** The reply's biggest-risk statement names one of the two
  risks the note gives. Only that statement counts: up to its first full stop, semicolon or line end, or
  the next line under a "Biggest risk" heading. A risk named as secondary, or mentioned afterwards, doesn't
  count. Before, "2.3 y" anywhere counted.

The patterns were fitted on 22 replies labelled by hand (6 Sonnet, all right; 16 Qwen, all wrong, including
the 9B's "Yes, if: ... PSM eventually works") and match all 22. Across every saved batch, 3 earlier runs are
credited (two Granite low runs from 09-30, one Granite run from 10-03). I read each one and all three are
genuine. Twenty-two unit tests in `nuru/tests/test_score_32.py`, including the counterexamples from the
PR #4 review: an explicit "does not meet", a qualified "theoretical estimate", a wrong audit whose risk
check used to pick up the 2.3-year figure from a later sentence (it scored 5/5 and now scores 3), a negated
risk ("the biggest risk is not the report rate..."), and a comparison in one sentence ("periodic attach
meets it, whereas always-connected does not"). A clause about always-connected alone no longer counts
against B; a clause naming both, or neither, still does.

## Rescored (test 32, points out of 5 per run)

| Batch | Old scorer | New scorer |
|---|---|---|
| suite-base-qwen4b (before the step-limit answer) | 5/25 | 5/25 |
| suite-after-qwen4b | 17/25 | 15/25 |
| suite-after-sonnet-qwen4b-t32 | 16/25 | 14/25 |
| suite-t32-qwen9b | 11/15 | 9/15 |
| suite-t32-sonnet-qwen9b | 9/15 | 8/15 |
| Sonnet reference, 64k output | 12/15 | 14/15 |

Across all 63 saved test 32 runs, the mean goes from 2.73 to 2.22. Qwen's remaining points are for reading
the notes right (237 days, 600 s keepalive) and replying. None comes from the conclusion or the risk.
Sonnet's one missed risk point is the run that names unmeasured standby current as the biggest risk.

## What it means

Retrieval and arithmetic are not the problem on test 32. The model adds a link the notes don't make
("periodic attach depends on PSM"), and neither a bigger local model nor Sonnet planning removes it, because
the local model still writes the answer. The structural fix is checking claims against the notes before
replying (standing reminder #7).
