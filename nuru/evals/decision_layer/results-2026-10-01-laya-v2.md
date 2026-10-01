# Laya categorisation, phase 2: relabelled to the user's rules and retrained, 2026-10-01

Phase 1 ([first slice](results-2026-10-01-laya-first-slice.md)) trained Laya on 300 requests labelled by my rules. The user's own 100-prompt test ([results](results-2026-10-01-user-test-set.md)) showed that the user labels some kinds of request differently. Phase 2 relabels the same 300 requests to the user's rules and retrains, with no new requests. It answers one question: how much of the gap was the labelling rules?

## The labelling rules (agreed 2026-10-01)

| Category | Rule |
|---|---|
| `answer` | Any request that needs no notes: general knowledge, terms, pleasantries. **New:** also on self-contained calculations (answer+calculate) and hard self-contained questions such as designs, proofs and plans (answer+expert). A standard explanation, such as how a Kalman filter works, is `answer` alone. |
| `expert` | Deep reasoning a small model can't do: designs, proofs, plans, **and judgement over notes** (contradictions, duplicates, consolidating notes into one, what's missing or blocking). Gathering, listing, summarising and side-by-side comparisons are `sweep` alone (the user's revisions to #2, 14, 16, 27, 37, 46 and 47). |
| `lookup` / `write` | A write to a note that already exists is lookup+write. **New:** adding to today's daily note is lookup+write too (the user's #42). Creating a new note, or creating today's daily note, is `write` alone (#79). |
| `calculate` | Numbers from one note: lookup+calculate. From many notes: sweep+calculate. "Which topics most often" counts as sweep+calculate (#11). |
| `clarify` | **New: alone,** whenever the subject can't be worked out from the request, even if it also asks for a write or a lookup ("Add that to my notes" is `clarify`, #97). |
| "this note" | The user's intent is that an earlier turn identified the note. Such prompts are labelled as lookups and reported separately as the `context` group in the user's set. Not yet reflected in the training data. |

## What changed

**Training data, [categorise_train.py](categorise_train.py) v2:** 86 of 300 labels changed. The v1 file was kept in the session's scratchpad for comparison.

| Change | Requests |
|---|---|
| calculate → answer+calculate (self-contained numbers) | 30 |
| expert → answer+expert (self-contained hard questions) | 35 |
| write+clarify / lookup+clarify → clarify | 11 |
| write → lookup+write (adding to today's daily note) | 6 |
| sweep → sweep+expert (contradictions; duplicate notes) | 2 |
| sweep+write → sweep+write+expert (merging notes into one summary) | 1 |
| sweep → sweep+calculate (topics written about most) | 1 |

Removed: 7 soft labels that contradicted the new labels, for example `answer: 0.2` on the derivation questions, which are now answer+expert.

The label mix is now: clarify 46, answer 35, answer+calculate 35, answer+expert 35, lookup 30, sweep 27, write 24, lookup+write 18, sweep+expert 10, sweep+calculate 9, lookup+calculate 8, lookup+expert 8, sweep+write 7, answer+write 7, sweep+write+expert 1.

**First test set, [prompt_categories.py](prompt_categories.py):** relabelled to the same rules so it stays comparable (14 changes):
- the 6 calculate prompts → answer+calculate;
- the 6 expert prompts → answer+expert;
- "Add a note about it." → `clarify`;
- "Add 'buy milk' to today's daily note." → lookup+write.

It now has 27 prompts with one label and 23 with two. Earlier results in the notes used the old labels.

**User's test set, [prompt_categories_user.py](prompt_categories_user.py):** unchanged since the user's 8 `expert` revisions. It stays test-only.

**Training, [train_categorise.py](train_categorise.py):** the same recipe, except **3 epochs instead of 4**, because v1's held-out loss was flat after epoch 2. The checkpoint records the data version (v2).

## Expected effect, and limits

- **It should fix:** the `answer` alongside other labels, vague requests as `clarify` alone, and today's daily note.
- **It probably won't fix much:**
  - **Judgement over notes as `expert`:** there are still only 3 sweep+expert-style judgement examples. The 12 `expert` misses on the user's set need new requests, which is the next phase.
  - **Short clear questions mistaken for `clarify`:** clarify examples are now 46 of 300, even more than before, and there are still no short-but-clear counterexamples.
  - **Three-label requests:** there is only 1 training example, against 11 in the user's set.

## Results

**Summary:** relabelling helped on my test set but **hurt on the user's**. With the same 300 requests, v2 learned the new `answer` rule too broadly and lost ground on `expert` over notes. **v1 remains the better checkpoint for the user's prompts.** The fix needs new requests, not more relabelling.

### Training (3 epochs, 17.7 minutes)

| Epoch | Training loss | Held-out loss | Held-out agreement |
|---|---|---|---|
| 0 (before) | - | 0.726 | 0.824 |
| 1 | 0.392 | 0.291 | 0.952 |
| 2 | 0.248 | 0.251 | 0.971 |
| 3 | 0.219 | 0.245 | 0.976 |

- **Yes/no temperature:** 1.189; the held-out loss at that temperature is 0.239.
- **Comparison with v1:** the held-out scores beat v1's best (0.277 / 0.957). But the held-out 30 are from the same 300 requests and the same author, so they mostly measure fit to my own labels.
- **Checkpoint:** `.runs/laya-categorise-20261001-185939/`.

### First test set (50, relabelled to the same rules)

Older runs were regraded against the new labels, with no new model calls. Each cell is exact / first pick right.

| Contender | Exact / first pick right | Recall | Precision | Labels per prompt |
|---|---|---|---|---|
| Original Laya yes/no | 10 / 31 | 0.44 | 0.61 | 1.08 |
| 3B `low` (mean of 10 runs) | 22.9 / 37.5 | - | 0.74 | - |
| v1 yes/no | 31 / 49 | 0.84 | 0.94 | 1.26 |
| **v2 yes/no** | **43 / 47** | **0.94** | 0.93 | 1.48 |

v2 has learned to add `answer` to self-contained calculations and hard questions, which v1 couldn't. Raw results: `.runs/categorise-20261001-190036.json`.

### The user's 100 prompts

| Subset (exact / first pick right) | Original | v1 yes/no | **v2 yes/no** | v2 choice | 3B `low` |
|---|---|---|---|---|---|
| **All 100** | 14 / 49 | **44 / 86** | 39 / 81 | 27 / 77 | 29 / 61 |
| Without overlap (97) | 14 / 49 | **43 / 83** | 36 / 78 | 26 / 75 | 29 / 60 |
| Single label (39) | 11 / 14 | 25 / **32** | **28** / **32** | 27 / 27 | 21 / 22 |
| Several labels (61) | 3 / 35 | **19 / 54** | 11 / 49 | 0 / 50 | 8 / 39 |
| `context` (18) | 1 / 6 | **5 / 15** | 3 / 12 | 2 / 12 | 1 / 9 |
| `convention` (5) | 0 / 2 | 1 / 4 | **3 / 5** | 0 / 4 | 0 / 4 |
| No group (75) | 13 / 41 | **37 / 65** | 31 / 62 | 24 / 60 | 28 / 48 |
| Recall / precision (all) | 0.33 / 0.47 | **0.70 / 0.82** | 0.67 / 0.78 | 0.51 / 0.77 | 0.53 / 0.61 |

**Correct labels found / wrong labels added, per category:**

| Category (gold) | v1 | v2 | 3B `low` |
|---|---|---|---|
| answer (10) | 6 / 1 | **10** / 10 | 4 / 0 |
| lookup (36) | 24 / 7 | 22 / **3** | 22 / 23 |
| sweep (47) | 30 / 3 | 32 / 5 | 28 / 18 |
| calculate (22) | **14** / 3 | 10 / 4 | 14 / 2 |
| write (20) | 15 / 2 | 14 / **0** | 15 / 4 |
| expert (32) | **20** / 5 | 13 / 2 | 7 / 3 |
| clarify (5) | 4 / 4 | **5** / 7 | 0 / 10 |

Raw results: `.runs/categorise-20261001-190127.json`, at 0.24 s per prompt.

### Why v2 got worse on the user's prompts

1. **`expert` became tied to "no notes needed".** In v2, 35 of the 54 `expert` examples are answer+expert (self-contained designs and proofs), and only 19 involve notes. Laya learned "hard" ≈ "answer", so judgement questions over notes now often come out as `answer` or `answer+expert`:
   - #31 "What is blocking Project X?", #49 "reconstruct my mental model", #90 "check conservation of energy" → `answer`;
   - #28 → answer+calculate+expert.

   `expert` found fell from 20 to 13, and `answer` was added wrongly 10 times (v1: 1).
2. **`clarify` grew.** Merging the vague write and lookup requests into `clarify` alone made it the largest single label (46 of 300). Short, clear sweeps now come out as `clarify` more often: #1 "What was I working on this time last year?", #39 and #40 "Who is waiting on me / Who am I waiting on?", #3 "What am I forgetting?".
3. **What did get better:**
   - `answer` is now found on all 10 prompts that need it;
   - the convention group went from 1 to 3 of 5;
   - wrong `lookup` and `write` labels fell (7 → 3 and 2 → 0);
   - single-label exact went from 25 to 28.

## Conclusion and next step

The agreed rules are right, but the data now has the wrong balance for them. The next phase should add new requests rather than relabel more:

| Add | Why |
|---|---|
| About 60–80 judgement-over-notes requests (sweep+expert and lookup+expert): contradictions, what's missing or blocking, changed opinions, checking or reproducing calculations | Break the "expert = no notes" link |
| About 40 short, clear sweep and lookup questions ("Who is waiting on me?", "What's due this week?") | Counter the 46 `clarify` examples |
| About 20–30 three-label requests | The user's set has 11, and training has 1 |
| About 20 "this note" requests labelled as lookups | The `context` group, as the user intends |
| Calculations over notes (lookup+calculate, sweep+calculate) phrased like the user's ("Recalculate…", "Average the five…", "Plot how…") | `calculate` slipped from 14 to 10 found |

Until then, **v1** (`.runs/laya-categorise-20261001-171153`) is the better checkpoint for the user's kind of prompts.
