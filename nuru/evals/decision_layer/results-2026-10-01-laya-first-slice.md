# Laya fine-tuned for yes/no categorisation: first slice, 2026-10-01

**Result:** after 22 minutes of training on 270 requests, Laya's yes/no categorisation on the 50 test prompts went from **19 to 43 exact label sets** and from **32 to 49 first picks right**. That puts it ahead of every 3B setup measured, including a simulated vote of 5 calls at `low` (35.4 / 40.9), while still taking 0.22 s per prompt. **Caveat:** I wrote both the training requests and the test prompts. The scores must be confirmed on prompts I didn't write before they can be trusted (see Caveats).

## What was done

- **Data:** [categorise_train.py](categorise_train.py) has 300 requests, written and labelled by Claude as the teacher:
  - 35 each for answer, expert and clarify, and 30 each for lookup, sweep, calculate and write;
  - 75 requests with two labels;
  - soft targets: 0.95 for each correct category and 0.04 for the rest, with in-between values on 32 borderline requests.

  None of the 50 test prompts is included (a code check enforces that), and 7 requests that copied a test prompt's template were reworded.
- **Training:** [train_categorise.py](train_categorise.py) fine-tunes the pinned English checkpoint (ModernBERT-large, 421M parameters) on MPS at full precision. It follows Convai's notebook: policy gradient on `proper_reward` plus soft cross-entropy, 4 epochs, an effective batch of 32, and cosine learning-rate decay. Of the 300 requests, 30 (210 items) were held out to watch for overfitting and to fit the yes/no temperature.
- **Checkpoint:** saved to `.runs/laya-categorise-20261001-171153/` (807 MB, gitignored). Nuru's Laya still loads the pinned checkpoint.
- **Test:** `categorise.py --only laya --laya-checkpoint .runs/laya-categorise-20261001-171153` produced `.runs/categorise-20261001-171304.json`.

## Training

| Epoch | Training loss | Held-out loss | Held-out agreement | Time |
|---|---|---|---|---|
| 0 (before) | - | 0.644 | 0.857 | - |
| 1 | 0.376 | 0.289 | 0.933 | 5.0 min |
| 2 | 0.279 | 0.281 | 0.952 | 10.6 min |
| 3 | 0.219 | 0.279 | 0.957 | 16.4 min |
| 4 | 0.198 | 0.277 | 0.957 | 22.4 min |

- **Fitted yes/no temperature:** 1.256. At that temperature the held-out loss is 0.265, with agreement unchanged at 0.957.
- **The lowest possible loss for these labels is about 0.18.** Agreement starts high because about 85% of items are "no", so always saying "no" would score about 0.85.
- **Speed and memory:** about 1.2 s per step of 8 items, faster than the smoke test's 2 s, with memory as in the [smoke test](results-2026-10-01-laya-training-smoke.md).
- **Reading the curve:**
  - Most of the gain came in epoch 1.
  - From epoch 2 the held-out loss is flat, while the training loss keeps falling (0.28 → 0.20), so the model starts memorising its 270 requests.
  - More, varied data will help more than more epochs.

## Results on the 50 test prompts

| Contender | Exact (of 50) | First pick right | Recall | Precision | Time per prompt |
|---|---|---|---|---|---|
| **Laya yes/no, fine-tuned** | **43** | **49** | 0.96 | **0.94** | 0.22 s |
| Laya choice, 1 call, fine-tuned* | 33 | 44 | 0.77 | 0.88 | 0.07 s |
| 3B `low`, vote of 5 (simulated) | 35.4 | 40.9 | - | 0.83 | about 8.5 s |
| 3B `low`, 1 call | 31.2 | 38.0 | 0.77 | 0.76 | 1.70 s |
| Laya yes/no, base | 19 | 32 | 0.55 | 0.63 | 0.22 s |
| Laya choice, 1 call, base | 15 | 23 | 0.38 | 0.46 | 0.07 s |

\* It was trained only on the yes/no question, yet the 7-option choice question also improved from 15 / 23 to 33 / 44. So the learning carries over to the other question format.

**The 11 two-label prompts:** yes/no went from 0 to **8 exact** and from 8 to 11 first picks right, with precision 0.97. It now gives a second label when one is needed (1.26 labels per prompt, against 1.08 before training).

**First pick right per category (single-label prompts), fine-tuned yes/no:**

| answer | lookup | sweep | calculate | write | expert | clarify |
|---|---|---|---|---|---|---|
| 5/6 (base 1) | 6/6 (base 3) | 6/6 (base 4) | 6/6 (base 3) | 3/3 (base 2) | 6/6 (base 6) | 6/6 (base 2) |

The `clarify` bias is gone: it was the first pick on 14 prompts before training, and is now first only where it belongs, plus on "Thanks, that's all for now".

**The 7 misses:**

| Prompt | Gold | Fine-tuned picks | Notes |
|---|---|---|---|
| Thanks, that's all for now. | answer | clarify | No category scored above 0.2, so the fallback took the likeliest one. No pleasantry like this was in the training data. |
| What lead time did the vendor quote for the SARA-R510S? | lookup | lookup, calculate (0.89) | "Lead time" sounds like a number to compute. |
| Convert 72°F to Celsius. | calculate | calculate, answer (0.80) | Arguably defensible. |
| Rename the tag #wip … in my reading list note. | lookup+write | lookup (write 0.41) | It missed the write. |
| Can you update the note? | clarify | clarify, lookup (0.56) | |
| What's the average of the run times in my training log? | lookup+calculate | + sweep (0.92) | "A log" could mean many entries, so sweep is defensible. |
| Go through all my project notes and propose a priority order … | sweep+expert | sweep | It missed the expert part. |

## Caveats

1. **The same author wrote both sets.** I wrote the 50 test prompts and the 300 training requests, so the trained Laya may partly have learned my phrasing and topics (dummyVault names like Priya, FreeRTOS, the supercap). The 43/50 is therefore an upper estimate. To confirm it, test on prompts the user writes, or on real requests from the logs (redacted first).
2. **Other question sets are untested.** Fine-tuning on one question can damage others, such as tool_gate, gate_write, rerank and check_claim. This checkpoint should only replace the English one in Nuru after a regression check, for example `replay.py` on the labelled decisions. The alternative is to keep it as a separate checkpoint used only for categorisation.
3. **The labels are unchecked.** Every label is mine, and no hand-check against labels by the user has been done.
4. **This was one run with one seed,** and the test set is small. The test is deterministic, but the training isn't: a different seed could shift the scores by a few points.

## Next

1. **A fresh test:** 50 prompts written by the user, or real requests from the logs. Run base and fine-tuned yes/no on both.
2. **A regression check** of the other question sets with the fine-tuned checkpoint.
3. **If both hold up,** scale the data to about 1,000 requests, adding more pleasantries, writes whose verb hides the edit ("rename", "mark"), and "propose / prioritise" requests for `expert`. Then decide whether categorisation gets its own checkpoint in Nuru's Laya server.
