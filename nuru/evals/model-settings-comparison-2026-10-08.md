# Model and settings comparison, 2026-10-07/08

Which model and settings should Nuru run? Every batch ran on the same code (branch `plan-executor` from `e4ff0d5`: core rule 8 and the expert example, final-answer calls that keep the tool list, the new recovery wording, Qwen `num_batch 256`). Each was switched by `NURU_BASE_MODEL`, the thinking level (`--tiers`) and the sampling alone, and scored by today's [score_runs.py](decision_layer/score_runs.py). Batches are in `decision_layer/.runs/`.

## Recommendation

- **Keep Qwen3.5 4B with thinking off and its current sampling.** No other setup scored meaningfully higher. Those that matched it took 1.5× the time or more.
- **Granite 4.2** stays the second choice. The 3B at `low` is 13 points behind. The 8B closes the gap on hard tests, but is about 3× slower and times out.
- **Granite now uses IBM's recommended sampling** (temperature 1.0, top_p 0.95; `6387889`). At `low` it scores within noise of the old 0.2 setting.

## Full suite: 34 tests × 3 (102 turns each)

| Model, thinking, sampling | Score /170 | Median turn | p90 turn | Total | Notes |
|---|---|---|---|---|---|
| **Qwen 4B, off, current** | **141.0** | **34 s** | 88 s | 85 min | Reference. 10-06 was 140.0 in 111 min, 10-04 131.7 in 78 min |
| Qwen 4B, off, presence penalty 1.5 | 140.7 | 36 s | 99 s | 82 min | Qwen's non-thinking general preset |
| Qwen 4B, on (`low` = on) | 142.0 | 62 s | 139 s | 131 min | **36 of 102 turns ended in reasoning with no answer** and were retried with thinking off |
| Qwen 9B, off | 142.3 | 59 s | 117 s | 129 min | |
| Granite 3B, low, temperature 0.2 | 128.3 | 43 s | 121 s | 109 min | 4 silent turns retried |
| Granite 3B, low, IBM (1.0 / 0.95) | 123.7 | 46 s | 148 s | 117 min | 5 silent turns retried |
| Granite 3B, off, IBM | 105.7 | 21 s | 51 s | 49 min | 22 runs made no tool call |
| Granite 3B, off, temperature 0.2 | 97.0 | 16 s | 61 s | 43 min | 35 runs made no tool call |

None of the batches had timeouts or crashes, and none made expert calls.

- **Qwen 4B with thinking off is the best value.** Thinking on and the 9B are within 1.3 points of it, which is within noise for 3 repeats.
- **Thinking on still often fails to answer** on Ollama 0.35.1, so the retry is doing much of the work in that batch's score.
- **Granite with thinking off fails.** It asks a clarifying question instead of searching first, for example "I need more context to determine who is right about the supercap". The Modelfile's warning against tool loops without thinking holds.
- **Granite uses fewer tools than Qwen:**

  | | Granite 3B, low, 0.2 | Qwen 4B, off |
  |---|---|---|
  | Tool calls per run | 2.5 | 4.2 |
  | `run_python` calls in 102 runs | 1 | 14 |

  Granite skips `calculate` on tests 11, 17, 22 and 27. These are among the tests where it scores lowest.
- **Granite's test 33 is worse than Qwen's.** No run says the expert isn't available, and two claim they attached the notes. Rule 8 and its example were tuned on Qwen only.

## Hard tests: 11 × 3

These are the 11 tests where Granite 3B with IBM sampling at `low` scored lowest: 11, 14, 17, 18, 19, 20, 21, 22, 25, 27 and 33.

| Model, thinking, sampling | Score /55 | Median turn | p90 turn | Timeouts |
|---|---|---|---|---|
| **Qwen 4B, off, current** (from the full suite) | **39.3** | 50 s | 106 s | 0 |
| Qwen 4B, off, presence penalty 1.5 | 39.3 | 43 s | 77 s | 0 |
| Qwen 4B, off, 1.0 / 0.95 / presence penalty 1.5 | 37.3 | 50 s | 86 s | 0 |
| Granite 8B, low, IBM | 34.7 of 50* | 140 s | 345 s | 3 |
| Granite 3B, high, IBM | 28.0 | 117 s | 322 s | 1 |
| Granite 3B, low, IBM (from the full suite) | 24.0 | 50 s | 148 s | 0 |

\*32 of 33 runs: the last run of test 33 was cut off when the batch's process was stopped. The score is out of 50 for the 10 complete tests.

- **The 8B fixes most of the 3B's weak tests** (14, 18, 19, 22: one to three points each), roughly matching Qwen 4B. But it's about 3× slower, and timed out on tests 21 and 33. A full 8B suite would take 6–7 hours, so it wasn't run.
- **`high` adds about 4 points to the 3B** at twice the time, with 8 silent turns retried.
- **Repeat penalty 1.1 and the 8B at `high` were queued, then cancelled.** They weren't run.

## Not settled

- Rule 8 and the expert example don't work on Granite (test 33). Prompt changes have been checked on Qwen only since 10-04. From now on, rerun the affected tests on both models.
- Qwen 4B's weakest tests stay weak in every setup: 20, 21, 22, 27 and 32. That's reasoning over notes, not settings.

## Batches

| Batch | Directory |
|---|---|
| Qwen 4B, off | `suite-full-qwen4b-1007-20261007-145818` |
| Qwen 4B, off, presence 1.5 | `suite-full-qwen4b-off-presence15-20261008-124018` |
| Qwen 4B, on | `suite-full-qwen4b-think-1007-20261007-222833` |
| Qwen 9B, off | `suite-full-qwen9b-1007-20261007-185223` |
| Granite 3B, low, 0.2 | `suite-full-granite3b-1007-20261007-164411` |
| Granite 3B, low, IBM | `suite-full-granite3b-ibm-low-1007-20261008-020713` |
| Granite 3B, off, IBM | `suite-full-granite3b-ibm-off-1007-20261008-005952` |
| Granite 3B, off, 0.2 | `suite-full-granite3b-off-1007-20261007-212731` |
| Hard tests: Granite 8B, low | `suite-granite8b-ibm-low-hard-20261008-072929` |
| Hard tests: Granite 3B, high | `suite-granite3b-ibm-high-hard-*` |
| Hard tests: Qwen presets A and B | `suite-qwen4b-off-presence15-hard-*`, `suite-qwen4b-off-reasoning-hard-*` |

The Qwen sampling variants were temporary edits to [profiles.yaml](../modelfiles/profiles.yaml), restored after each batch. The Granite IBM batches ran on the Modelfile change later committed as `6387889`.
