# Laya categorisation, phase 3: 190 new requests, 2026-10-01

**Result:** on the user's 100 prompts, Laya v3 reaches **56 exact label sets and 94 first picks right**:
- v1: 44 / 86;
- v2: 39 / 81;
- 3B at `low`: 29 / 61.

It also gets 46 / 50 on the first test set, at 0.24 s per prompt. Recall rose to 0.88 with precision at 0.87, and it no longer marks short, clear questions as `clarify`.

**Caveat:** the new requests target the kinds of prompt the user's set exposed, so that set is now less independent. A fresh test set is the next check.

## What was done

- **Data, v3 = 490 requests:**
  - the 300 relabelled v2 requests ([categorise_train.py](categorise_train.py));
  - **190 new ones** ([categorise_train_v3.py](categorise_train_v3.py)), written and labelled by Claude under the agreed rules.

  All 190 are listed for the user's audit in [categorise-train-v3-audit.md](categorise-train-v3-audit.md).

  | Group | Added | Labels |
  |---|---|---|
  | Judgement over many notes | 40 | sweep+expert |
  | Judgement over one note | 30 | lookup+expert |
  | Short, clear questions | 40 | 25 sweep, 15 lookup |
  | Three-label requests | 25 | sweep+calculate+expert 6, lookup+calculate+expert 5, sweep+write+expert 5, lookup+write+expert 4, lookup+calculate+write 5 |
  | "this note" requests (an earlier turn identified the note) | 20 | lookup 8, lookup+write 5, lookup+calculate 4, lookup+expert 3 |
  | Calculations over notes | 25 | lookup+calculate 13, sweep+calculate 12 |
  | Pleasantries | 10 | answer |

- **Balance after the additions:**
  - `expert` with notes outnumbers self-contained `expert` 112 to 35 (in v2 it was 19 to 35);
  - `clarify` is 46 of 490 (9%, down from 15%);
  - 26 three-label requests (down from 1).
- **Kept apart from the tests:**
  - No addition is a copy or near-copy of a test prompt: all were checked against both test sets at a similarity cut-off of 0.7, and a code check rejects any user test prompt in the training data.
  - The user's set keeps its 3-prompt `overlap` group from earlier.
- **Training:** the same recipe for 3 epochs: 441 requests for training (3,087 items), 49 held out (343 items). It took 30.2 minutes.
- **Checkpoint:** `.runs/laya-categorise-20261001-194043/`.
- **Raw results:** `.runs/categorise-20261001-194136.json` (first set) and `-194226.json` (the user's set).

## Training

| Epoch | Training loss | Held-out loss | Held-out agreement |
|---|---|---|---|
| 0 (before) | - | 0.886 | 0.790 |
| 1 | 0.369 | 0.260 | 0.962 |
| 2 | 0.251 | 0.279 | 0.959 |
| 3 | 0.210 | 0.261 | 0.962 |

The held-out loss was best after epochs 1 and 3, with a small bump in between. The training loss kept falling. The fitted yes/no temperature is 1.218, giving a held-out loss of 0.252. One import bug (a circular import between the training data and the user test file) stopped the first launch. It was fixed by keeping the leak check in `prompt_categories_user.py` only.

## Results

### First test set (50, current labels)

| Checkpoint | Exact / first pick right | Recall | Precision | Labels per prompt |
|---|---|---|---|---|
| v1 | 31 / 49 | 0.84 | 0.94 | 1.26 |
| v2 | 43 / 47 | 0.94 | 0.93 | 1.48 |
| **v3** | **46 / 50** | **0.99** | **0.97** | 1.50 |

### The user's 100 prompts

| Subset (exact / first pick right) | v1 | v2 | **v3 yes/no** | v3 choice (1 call) | 3B `low` |
|---|---|---|---|---|---|
| **All 100** | 44 / 86 | 39 / 81 | **56 / 94** | 32 / 85 | 29 / 61 |
| Without the 3 overlap prompts (97) | 43 / 83 | 36 / 78 | **53 / 91** | 31 / 83 | 29 / 60 |
| Single label (39) | 25 / 32 | **28** / 32 | 25 / **35** | 32 / 32 | 21 / 22 |
| Several labels (61) | 19 / 54 | 11 / 49 | **31 / 59** | 0 / 53 | 8 / 39 |
| Three labels (11) | 2 / 10 | 0 / 5 | **3 / 11** | 0 / 10 | 1 / 5 |
| `context` (18) | 5 / 15 | 3 / 12 | **9 / 17** | 2 / 15 | 1 / 9 |
| `convention` (5) | 1 / 4 | 3 / 5 | **5 / 5** | 0 / 4 | 0 / 4 |
| No group (75) | 37 / 65 | 31 / 62 | **40 / 70** | 29 / 65 | 28 / 48 |
| Recall / precision (all) | 0.70 / 0.82 | 0.67 / 0.78 | **0.88 / 0.87** | 0.57 / 0.85 | 0.53 / 0.61 |
| Labels per prompt (gold: 1.72) | 1.38 | 1.37 | 1.73 | 1.00 | 1.50 |

**Correct labels found / wrong labels added, per category:**

| Category (gold) | v1 | v2 | **v3** | 3B `low` |
|---|---|---|---|---|
| answer (10) | 6 / 1 | 10 / 10 | 10 / 9 | 4 / 0 |
| lookup (36) | 24 / 7 | 22 / 3 | **30 / 1** | 22 / 23 |
| sweep (47) | 30 / 3 | 32 / 5 | **42 / 1** | 28 / 18 |
| calculate (22) | 14 / 3 | 10 / 4 | **18** / 6 | 14 / 2 |
| write (20) | 15 / 2 | 14 / 0 | **16 / 0** | 15 / 4 |
| expert (32) | 20 / 5 | 13 / 2 | **25** / 10 | 7 / 3 |
| clarify (5) | 4 / 4 | 5 / 7 | **5 / 0** | 0 / 10 |

## What improved and what didn't

**What improved:**
- **`expert` over notes:** 25 of 32 found, up from 13 in v2.
- **`clarify`:** no wrong labels at all. The short questions that v2 sent to `clarify` ("Who is waiting on me?", "What was I working on this time last year?") are now right.
- **Multi-label and context prompts:** exact matches rose from 11 to 31 and from 3 to 9.
- **Lookup and sweep:** found 30 and 42, with only 1 wrong label each.
- **Labels per prompt (1.73)** now matches the user's average (1.72).

**What's still wrong (44 misses; most are one label too many or too few):**
1. **`expert` is now slightly over-applied (10 wrong labels):**
   - On standard explanations the user labelled `answer` alone: #55 Rust ownership, #56 correlation vs causation, #58 Kalman filter, #60 eigenvectors. Each came out answer+expert.
   - On the sweeps the user revised to `sweep` alone: #14 Jungle Gyms, #16 compare approaches, #26 extract equations, #27 equation dependencies. The line between "summarise" and "judge" is still blurry for Laya.
2. **`answer` turns up on note requests (9 wrong labels):** for example #32 "highest-leverage thing", #34 "projects that haven't moved", #37 "What can I finish in 30 minutes?" (`answer` alone) and #72 "Mark the task as complete" (answer+write).
3. **Some judgement prompts still lack `expert`:** #3 "What am I forgetting?", #48 "What did I learn this month…", #23 "Where did this number come from?", #29 "Reproduce the calculation…", #88 correlation, #90 conservation-of-energy check.
4. **The `calculate` the user puts on age and recency questions is missed:** #11, #12 and #34, where the user labelled "unusually often", "longest-running" and "haven't moved in a month" as needing `calculate`.
5. **Writes whose verb hides the edit:** #41 "Remind me Friday…" and #76 "Rename the note…" came out without lookup or without write.

## Caveats

1. **Less independent test.** The additions target the patterns the user's set exposed. No addition is a near-copy, but on those patterns the user's 100 now partly measure what I aimed the data at. A third test set, new prompts from the user or real redacted requests from the logs, is needed to confirm the gain.
2. **The labels are unaudited.** All 490 labels are mine; the user will audit the 190 new ones ([audit list](categorise-train-v3-audit.md)).
3. **Laya's other questions are unchecked.** tool_gate, gate_write, rerank and check_claim haven't been regression-tested with any fine-tuned checkpoint. Nuru still loads the pinned checkpoint.
4. **One run, one seed.**

## Next

1. **User's audit** of the 190 additions. Apply corrections and retrain if many change.
2. **A third, fresh test set**, to confirm v3's gain on prompts nobody aimed the data at.
3. **Small, targeted additions:**
   - standard explanations as `answer` alone;
   - plain extraction and comparison sweeps without `expert`;
   - age and recency questions as sweep+calculate;
   - writes with hidden verbs (remind, rename) as lookup+write.
4. **A regression check of the other question sets**, then decide between a separate categorisation checkpoint and one shared checkpoint.
