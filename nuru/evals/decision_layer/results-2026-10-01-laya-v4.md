# Laya categorisation, phase 4: reasoning without the expert, 2026-10-01

**Result:** on the user's 100 prompts, Laya v4 reaches **65 exact label sets**, the best so far (v3 56, v1 44, 3B at `low` 29). It gets **90 first picks right**, which is 4 fewer than v3. It overuses `expert` and `answer` half as often as v3 (5 wrong labels each, down from 10 and 9). On the first test set it scored 43 / 49 (v3: 46 / 50).

## What was done

- **Data, v4 = 622 requests:** v3's 490 (all 190 v3 additions approved by the user) plus **132 new requests** in [categorise_train_v4.py](categorise_train_v4.py). The new ones are worth some reasoning but don't need the expert, as the user asked, and all are labelled without `expert`. Audit list: [categorise-train-v4-audit.md](categorise-train-v4-audit.md).

  | Group | Added | Labels |
  |---|---|---|
  | Explanations that take some thought ("Why does ice float?") | 30 | answer |
  | Word problems with a step or two | 12 | answer+calculate |
  | A short inference from one note ("Does my lease note say I can have pets?") | 25 | lookup |
  | Light synthesis across notes (summaries, side-by-side comparisons, grouping) | 30 | sweep |
  | Dates, ages and simple numbers from notes | 20 | lookup+calculate 10, sweep+calculate 10 |
  | Light edits that take a little thought (sort, reorder, retitle, tidy) | 15 | lookup+write 10, sweep+write 4, write 1 |

  - **Borderline requests** carry a small soft `expert` of 0.1–0.2. There are 19 soft entries in all.
  - **Kept apart from the tests:** 8 requests were reworded to stay outside the 0.7 similarity cut-off against both test sets, mostly away from the "What's the difference between X and Y?" template.
  - **Balance:** 147 requests now have `expert` and 475 don't.
- **Training:** the same recipe for 3 epochs: 560 requests for training (3,920 items), 62 held out (434 items). It took 35.4 minutes.
- **Checkpoint:** `.runs/laya-categorise-20261001-233424/`.
- **Raw results:** `.runs/categorise-20261001-233523.json` (first set) and `-233609.json` (the user's set).

| Epoch | Training loss | Held-out loss | Held-out agreement |
|---|---|---|---|
| 0 (before) | - | 0.717 | 0.823 |
| 1 | 0.367 | 0.357 | 0.933 |
| 2 | 0.251 | 0.258 | 0.968 |
| 3 | 0.222 | 0.256 | 0.970 |

The fitted yes/no temperature is 1.221, giving a held-out loss of 0.247. The held-out set differs from v3's, so the numbers aren't directly comparable.

## Results

### First test set (50)

| Checkpoint | Exact / first pick right | Recall | Precision |
|---|---|---|---|
| v1 | 31 / 49 | 0.84 | 0.94 |
| v2 | 43 / 47 | 0.94 | 0.93 |
| v3 | **46 / 50** | **0.99** | **0.97** |
| v4 | 43 / 49 | 0.95 | 0.95 |

### The user's 100 prompts

| Subset (exact / first pick right) | v1 | v3 | **v4 yes/no** | v4 choice (1 call) | 3B `low` |
|---|---|---|---|---|---|
| **All 100** | 44 / 86 | 56 / **94** | **65** / 90 | 29 / 75 | 29 / 61 |
| Without the 3 overlap prompts (97) | 43 / 83 | 53 / **91** | **62** / 87 | 28 / 73 | 29 / 60 |
| Single label (39) | 25 / 32 | 25 / **35** | **31** / 34 | 29 / 29 | 21 / 22 |
| Several labels (61) | 19 / 54 | 31 / **59** | **34** / 56 | 0 / 46 | 8 / 39 |
| Three labels (11) | 2 / 10 | 3 / 11 | **5 / 11** | 0 / 8 | 1 / 5 |
| `context` (18) | 5 / 15 | 9 / **17** | 9 / 14 | 2 / 11 | 1 / 9 |
| `convention` (5) | 1 / 4 | **5 / 5** | 4 / 5 | 0 / 4 | 0 / 4 |
| No group (75) | 37 / 65 | 40 / **70** | **50** / 69 | 26 / 59 | 28 / 48 |
| Recall / precision | 0.70 / 0.82 | **0.88** / 0.87 | 0.84 / **0.89** | 0.51 / 0.75 | 0.53 / 0.61 |
| Labels per prompt (gold 1.72) | 1.38 | 1.73 | 1.61 | 1.00 | 1.50 |

**Correct labels found / wrong labels added, per category:**

| Category (gold) | v1 | v3 | **v4** | 3B `low` |
|---|---|---|---|---|
| answer (10) | 6 / 1 | 10 / 9 | 10 / **5** | 4 / 0 |
| lookup (36) | 24 / 7 | **30** / 1 | 28 / 1 | 22 / 23 |
| sweep (47) | 30 / 3 | **42 / 1** | 41 / 2 | 28 / 18 |
| calculate (22) | 14 / 3 | **18** / 6 | 16 / 5 | 14 / 2 |
| write (20) | 15 / 2 | 16 / 0 | **19 / 0** | 15 / 4 |
| expert (32) | 20 / 5 | **25** / 10 | 23 / **5** | 7 / 3 |
| clarify (5) | 4 / 4 | 5 / 0 | 5 / 1 | 0 / 10 |

## What changed from v3

**Fixed by v4 (14 prompts):**
- The sweeps the user revised away from `expert`: #14 Jungle Gyms, #26 extract equations.
- The standard explanations: #56 correlation vs causation, #58 Kalman filter, #60 eigenvectors, which are now `answer` alone.
- Writes with hidden verbs: #76 rename, #78 append to tomorrow's agenda.
- Also #5, #24, #25, #30, #33, #68 and #86.

**Broken by v4 (5 prompts):**
- #6 "What have I changed my mind about…" and #17 "Turn my scattered notes into a proper technical note" lost `expert`.
- #52 "Convert 72°F to Celsius" lost `calculate`.
- #63 "Find my notes from the meeting with Alice on September 12" became `sweep` instead of `lookup`.
- #87 signal-to-noise picked up `expert`.

**Overall:**
- **`expert` and `answer` are no longer overused:** 5 wrong labels each, down from 10 and 9. That was this phase's aim.
- **The cost:** a few judgement prompts got pulled under the line, so `expert` found went from 25 to 23, and the first pick slipped 4 points (94 → 90).
- **Exact sets rose by 9**, and three-label prompts went from 3 to 5.

**Still missed (35 prompts):**
- **`expert` on "what am I missing"-style judgement:** #3 "What am I forgetting?", #48 "What did I learn this month…", #31 "What is blocking Project X?" (now `lookup` alone), #88 correlation and #90 conservation-of-energy check.
- **Age and recency `calculate`** (#11, #12, #34): still missed, even with 20 date examples.
- **"this note" prompts:** first pick right dropped from 17 to 14. #23 "Where did this number come from?" is back to `clarify`, and #41 "Remind me Friday if I still haven't completed this task" got `calculate`.

## Caveats

1. **v3 vs v4 is partly a trade, not a clean win.** v4 has more exact sets and fewer wrong labels, while v3 had more first picks right and found more. Which matters more depends on use:
   - **routing** (pick the main tool family) favours a correct first pick;
   - **tool narrowing** (give exactly the needed tools) favours exact sets and precision.
2. **The user's set is less independent** than at the start, because v3's and v4's data target its patterns. The first set dipped slightly (46 / 50 → 43 / 49), which may be noise or a small cost of the new batch.
3. **The labels are unaudited** for v4's 132 additions (v3's 190 were approved).
4. **Laya's other questions are still unchecked,** and this is one run with one seed.

## Next

1. **A fresh third test set:** this is now the main gap. Neither test set can separate v3 from v4 reliably any more.
2. **Targeted additions:** "what am I missing / forgetting / what changed" as sweep+expert, recency and "how long" questions as sweep+calculate, and "this number / this task" context prompts.
3. **The regression check of the other question sets,** then the separate-vs-shared checkpoint decision.
