# Categorisation on the user's 100 prompts, 2026-10-01

**Result:** on 100 prompts the user wrote and labelled ([prompt_categories_user.py](prompt_categories_user.py)), the fine-tuned Laya keeps a clear lead:
- **Fine-tuned Laya yes/no:** 40 exact label sets and 87 first picks right, at 0.21 s per prompt.
- **3B at `low`:** 24 / 61, at 1.4 s per prompt.
- **Original Laya:** 14 / 50.

So fine-tuning helps on prompts I didn't write, not just on my own phrasing. But the score is lower than on my test set (43/50 exact), and the misses show where the training data's labelling differs from the user's. Most of all, the user tags `expert` on judgement and synthesis over notes, which my training data didn't.

## The set

The 100 prompts are hypothetical, with random names. The user's labels are the right answers as written.

- **Mostly multi-label:** 32 prompts have one label, 56 have two and 12 have three (1.8 labels per prompt on average). `sweep` appears 47 times, `expert` 40, `lookup` 36, `calculate` 22, `write` 20, `answer` 10 and `clarify` 5.
- **Groups reported separately:**
  - `context` (18): "this note / task / number…" prompts, which the user intends to follow an earlier turn that identified the note.
  - `convention` (6): the user's labelling where the training data used a different rule. That's `answer` alongside `calculate` or `expert` (#52, 54, 57, 58, 59), and today's daily note as lookup+write (#42).
  - `overlap` (3): matches with the training data. #96 "Fix it." and #97 "Add that to my notes." match word for word, and #57 is a growth-rate question close to a training request.

The runs were one after another: the original Laya, the fine-tuned Laya (`.runs/laya-categorise-20261001-171153`), and the 3B at `low` (one run, about ±3 points of noise). The raw results are in `.runs/categorise-20261001-182041.json`, `-182129.json` and `-182418.json`.

## Results

| Subset | Original Laya yes/no | **Fine-tuned Laya yes/no** | Fine-tuned Laya choice (1 call) | 3B `low` |
|---|---|---|---|---|
| **All 100:** exact / first pick right | 14 / 50 | **40 / 87** | 22 / 74 | 24 / 61 |
| All 100: recall / precision | 0.32 / 0.49 | **0.68 / 0.84** | 0.46 / 0.74 | 0.51 / 0.61 |
| Without the 3 overlap prompts (97) | 14 / 50 | **39 / 84** | 21 / 72 | 24 / 60 |
| Single label (32) | 10 / 11 | 21 / **27** | **22** / 22 | 17 / 18 |
| Several labels (68) | 4 / 39 | **19 / 60** | 0 / 52 | 7 / 43 |
| `context` (18) | 1 / 7 | **6 / 15** | 1 / 11 | 1 / 9 |
| `convention` (6) | 0 / 2 | 1 / 5 | 0 / 5 | 0 / 5 |
| No group (74) | 13 / 41 | **32 / 65** | 20 / 57 | 23 / 47 |
| Labels per prompt (gold: 1.8) | 1.13 | 1.38 | 1.00 | 1.50 |
| Time per prompt | 0.22 s | 0.21 s | 0.07 s | 1.41 s |

**Correct labels found / wrong labels added, per category, across all 100:**

| Category (gold count) | Original Laya yes/no | Fine-tuned Laya yes/no | 3B `low` |
|---|---|---|---|
| answer (10) | 0 / 13 | 6 / 1 | 4 / 0 |
| lookup (36) | 8 / 1 | 24 / 7 | 22 / 23 |
| sweep (47) | 19 / 6 | 30 / 3 | 28 / 18 |
| calculate (22) | 7 / 3 | 14 / 3 | 14 / 2 |
| write (20) | 14 / 1 | 15 / 2 | 15 / 4 |
| expert (40) | 8 / 4 | **22 / 3** | 7 / 3 |
| clarify (5) | 0 / 29 | 4 / 4 | 0 / 10 |

## What the misses show

1. **`expert` means something broader to the user than in my training data.** That's 18 of the fine-tuned Laya's 40 expert labels missed, and most at p ≈ 0.07, a confident "no". The user tags `expert` on judgement and synthesis over notes:
   - "Give me a 5-minute briefing…", "Find contradictions in my notes", "What is blocking Project X?";
   - "What did I learn this month that would have surprised me…", "Which notes are becoming dead ends?";
   - "Generate my weekly review".

   My training data used `expert` for designs, proofs, plans and explicit "analyse / propose" requests, and labelled plain summaries and audits as `sweep` alone. Laya learned my rule faithfully. The fix is in the data, not the model: label synthesis, judgement, prioritising and finding contradictions as `+expert`.
2. **Short, clear questions get mistaken for vague ones.** The fine-tuned Laya said `clarify` for "Who is waiting on me?", "Who am I waiting on right now?", "What am I forgetting?" and "Where did this number come from?". My `clarify` training examples were all short questions, so Laya partly learned "short means unclear". The next data needs short questions that are perfectly clear.
3. **`answer` alongside other labels** (the user's convention) is mostly missed:
   - #54 and #57 got `calculate` alone, and #59 got `expert` alone. That follows my training rule, not the user's.
   - #55 "Give me three reasons Rust's ownership…" got `expert` instead of `answer`.
4. **Under-labelling:** Laya gives 1.38 labels per prompt against 1.8 in the user's labels. That's why its recall (0.68) trails its precision (0.84). Its labels are usually right, but some needed ones are missing. Almost all of the gap comes from points 1 and 3.
5. **`context` prompts were handled well:** 15/18 first picks right. Laya mostly treats "this note" as a lookup rather than `clarify`, which matches the user's intent.
6. **The overlap prompts barely matter:** removing them changes the fine-tuned Laya's score from 40/87 to 39/84 (out of 97).

## Revised labels (later on 2026-10-01): `expert` removed from 8 prompts

The user removed `expert` from #2 (5-minute briefing), #14 (what I know about Jungle Gyms), #16 (compare approaches A, B and C), #27 (which equations use this variable), #37 (what I can finish in 30 minutes), #46 (dead-end notes), #47 (weekly review) and #58 (how a Kalman filter works). Those are summaries, side-by-side comparisons, link audits and standard explanations, which a small model can do. That leaves 32 `expert` labels; 39 prompts now have one label, 50 have two and 11 have three. #58 is now `answer` alone, so it left the `convention` group (now 5 prompts). The three saved runs were regraded against the new labels, with no new model calls.

| Subset (exact / first pick right; recall, precision) | Original Laya yes/no | **Fine-tuned Laya yes/no** | Fine-tuned Laya choice (1 call) | 3B `low` |
|---|---|---|---|---|
| **All 100** | 14 / 49 (0.33, 0.47) | **44 / 86 (0.70, 0.82)** | 27 / 74 (0.49, 0.74) | 29 / 61 (0.53, 0.61) |
| Without the 3 overlap prompts (97) | 14 / 49 | **43 / 83** | 26 / 72 | 29 / 60 |
| Single label (39) | 11 / 14 | 25 / **32** | **27** / 27 | 21 / 22 |
| Several labels (61) | 3 / 35 | **19 / 54** | 0 / 47 | 8 / 39 |
| `context` (18) | 1 / 6 | **5 / 15** | 2 / 11 | 1 / 9 |
| `convention` (5) | 0 / 2 | 1 / 4 | 0 / 4 | 0 / 4 |
| No group (75) | 13 / 41 | **37 / 65** | 24 / 58 | 28 / 48 |

**`expert` labels found / wrong `expert` labels added:** fine-tuned Laya 20/32 found, 5 added (was 22/40 and 3); 3B 7/32 and 3; original Laya 6/32 and 6. Other categories are unchanged from the table above.

**Takeaways:**
- **The fine-tuned Laya's exact matches rise from 40 to 44, and the 3B's from 24 to 29.** The lead holds at 15 exact and 25 first-pick points.
- **Its first pick right slips from 87 to 86.** On #58 its pick of `expert` alone was right under the old labels.
- **Its remaining 12 `expert` misses are the clear judgement tasks:** "What am I forgetting?", "Find contradictions", "What is blocking Project X?", "What did I learn this month…", the inconsistent-numbers check, reproducing a calculation, the conservation-of-energy check, and a correlation with interpretation. Those are what the next training data needs to teach.

## Compared with the first test set (50 prompts I wrote)

| | Fine-tuned Laya yes/no | 3B `low` |
|---|---|---|
| My 50: exact / first pick right | 43/50 (86%) / 49/50 (98%) | 31.2/50 (62%) / 38/50 (76%) |
| User's 100: exact / first pick right | 40/100 (40%) / 87/100 (87%) | 24/100 (24%) / 61/100 (61%) |

- **Both contenders drop on the user's set.** It is mostly multi-label, and its labelling differs from mine.
- **The gap between Laya and the 3B holds:** 26 points on first pick for both sets.
- **The first pick generalises well (87%).** The full label set does less well (40%), so the remaining work is mainly in which second and third labels apply.

## Next

1. **Align the labelling.** Agree with the user what `expert` covers (judgement and synthesis over notes, not only designs and proofs), and whether `answer` goes alongside `calculate` or `expert`. Then relabel the 300 training requests to match.
2. **Add data for the misses:**
   - synthesis requests ("briefing", "what am I forgetting", "contradictions", "weekly review");
   - short but clear questions;
   - self-contained calculations and explanations under the user's convention.
3. **Retrain**, still on this Mac (about 22 minutes for 300 requests), then test on both sets. The user's 100 stay test-only.
4. **Still to do:** the regression check of Laya's other question sets before this checkpoint goes anywhere near Nuru.
