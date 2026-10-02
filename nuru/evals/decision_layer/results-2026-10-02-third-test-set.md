# Categorisation on the user's third test set (#101-200), 2026-10-02

**Result:** on 100 fresh prompts that no training round was aimed at, fine-tuning holds up strongly:
- **Original Laya:** 19 exact label sets, 60 first picks right.
- **Fine-tuned checkpoints:** v1 61 / 92, **v3 68 / 97** and **v4 69 / 94**.
- **3B at `low`:** 45 / 75.

**v3 and v4 tie on exact sets.** v3 is ahead on first picks (97 vs 94), recall (0.92 vs 0.86) and `clarify` (10 / 10 vs 8 / 10); v4 adds fewer wrong labels. Most of v4's 9-point lead on the second set came from data aimed at that set. On independent prompts, **v3 is the slightly better all-round checkpoint**, and v4 is the more conservative one.

## The set

[prompt_categories_third.py](prompt_categories_third.py) holds 100 prompts written and labelled by the user after the v1–v4 training rounds. They cover new topics (machine learning, signal processing, lab work, travel, workshops) in the user's own phrasing.

- **Labels:** 42 prompts have one label, 55 have two and 3 have three (1.61 labels per prompt on average).
- **Label counts:** lookup 39, sweep 32, expert 29, calculate 21, answer 15, write 15, clarify 10.
- **Groups:**
  - `context` (21): prompts that refer to "this note / these experiments / this anomaly…".
  - `overlap`: empty. No prompt is within the 0.85 similarity cut-off of any of the 622 training requests. Four are template-close (the "What is the difference between…" form, "Make it better" against "Make it shorter", and "Do the thing we talked about earlier" against "Add the thing we talked about").
- **Repeats from my first test set:** #114 (mutex vs semaphore) and #195 ("Make it better"). Harmless, because neither set is used for training.

**Runs:** original Laya; checkpoints v1 (`laya-categorise-20261001-171153`), v3 (`-194043`) and v4 (`-233424`); and the 3B at `low` (one run). v2 was skipped because v3 and v4 supersede it. Raw results are in `.runs/categorise-20261002-115314.json` (original), `-115404` (v1), `-115458` (v3), `-115552` (v4) and `-115915` (3B).

**One bug:** the first attempt crashed in the summary because the empty `overlap` group divided by zero, before anything was saved. `categorise.py` now skips empty groups, and all five runs were repeated.

## Results

| Subset (exact / first pick right) | Original | v1 | **v3** | **v4** | v4 choice (1 call) | 3B `low` |
|---|---|---|---|---|---|---|
| **All 100** | 19 / 60 | 61 / 92 | **68 / 97** | **69** / 94 | 27 / 75 | 45 / 75 |
| Recall / precision | 0.44 / 0.60 | 0.85 / **0.92** | **0.92** / 0.88 | 0.86 / 0.87 | 0.51 / 0.75 | 0.68 / 0.77 |
| Single label (42) | 17 / 17 | **34** / 38 | 29 / **40** | 30 / 36 | 27 / 27 | **34** / 35 |
| Several labels (58) | 2 / 43 | 27 / 54 | **39** / 57 | **39 / 58** | 0 / 48 | 11 / 40 |
| `context` (21) | 2 / 15 | 7 / 18 | **13 / 21** | 12 / **21** | 1 / 15 | 6 / 12 |
| Not context (79) | 17 / 45 | 54 / 74 | 55 / **76** | **57** / 73 | 26 / 60 | 39 / 63 |
| Labels per prompt (gold 1.61) | 1.17 | 1.45 | 1.72 | 1.60 | 1.00 | 1.45 |
| Time per prompt | 0.2 s | 0.2 s | 0.24 s | 0.25 s | 0.07 s | 1.77 s |

**Correct labels found / wrong labels added, per category:**

| Category (gold) | Original | v1 | v3 | v4 | 3B `low` |
|---|---|---|---|---|---|
| answer (15) | 1 / 6 | 10 / **0** | **15** / 7 | **15** / 4 | 4 / 0 |
| lookup (39) | 13 / 1 | 26 / 5 | **29** / 3 | 28 / **1** | 26 / 11 |
| sweep (32) | 18 / 7 | 25 / 7 | **31 / 3** | 28 / 9 | 27 / 15 |
| calculate (21) | 10 / 5 | 18 / 1 | **20** / 4 | 19 / **0** | 15 / 2 |
| write (15) | **15** / 4 | **15** / 1 | 14 / **0** | **15** / 2 | 14 / 6 |
| expert (29) | 12 / 3 | 25 / **2** | **27** / 9 | 25 / 6 | 10 / 3 |
| clarify (10) | 0 / 22 | **10 / 0** | **10 / 0** | 8 / 0 | 6 / 6 |

## What it shows

1. **Fine-tuning generalises.** On prompts nobody aimed the data at, the original Laya gets 19 / 60 and every fine-tuned checkpoint gets 61–69 / 92–97. That's more than three times the exact sets, and it holds across every category. Laya also beats the 3B by 23–24 exact and about 20 first picks, at about 1/7 of the time.
2. **v3 vs v4 is a near-tie.**
   - v4 fixed what it was built for: standard explanations as `answer` alone (#101, 103, 106, 109) and fewer wrong `answer` and `lookup` labels.
   - But v4 also adds `sweep` to some plain lookups (#118, 122, 127) and misses 2 of 10 `clarify` prompts (#197 "Update my notes with what happened" → `sweep`, #200 "Change the project plan accordingly" → `write`).
   - v3's main fault is still `expert` overuse: 9 wrong labels (#101, 103, 106, 109, 120, 121, 130, 139, 157).
3. **v1 is surprisingly strong for its size.** Trained on 300 requests under my original rules, it has the highest precision (0.92) and the fewest wrong `expert` labels (2). Its weakness is that it gives too few labels (recall 0.85), so it got only 27 of 58 multi-label prompts exact. The later rounds mainly taught it to add the second label.
4. **The 3B does better on this set than on the second** (45 / 75 against 29 / 61), because this set is less multi-label. Its weak spots are the same: `expert` is found on only 10 of 29, and it adds wrong `lookup` and `sweep` labels (11 and 15).
5. **Errors common to v3 and v4,** worth targeting next:
   - Lookups that sound like judgement pick up `expert`: #121 "Find the note where I documented the PostgreSQL migration problem", #130 "What reason did I write down for rejecting the first enclosure design?".
   - `calculate` is missed on counting and ranking (#141 "referenced most frequently") and on a loss computed between two values (#150, where `lookup` was also missed).
   - Writes that read from several notes get `sweep` added: #170 "Add backlinks from these experiment notes…".
   - Merge-into-one-note gets `write` alone (#173), missing lookup and expert.
   - `answer` is added to some judgement questions over notes: #180, #183, #189.

## Which checkpoint

- **For routing** (pick the main kind of work, ask the user when unclear): **v3**. It has the most first picks right (97 / 100), finds `clarify` on 10 / 10 and has the highest recall.
- **For tool narrowing** (give exactly the right tools): v3 and v4 are level on exact sets (68 vs 69). v4 adds fewer wrong labels in some categories (`answer`, `lookup`, `calculate`) and more in others (`sweep`).
- **If one checkpoint has to be picked now, v3** is the safer default. But the gap is within noise for 100 prompts.

## Next

1. **A small targeted round** on top of v4's data, for the shared errors above:
   - lookups phrased with "reason" or "problem" (not `expert`);
   - counting and ranking as `+calculate`;
   - writes that read several notes (lookup+write unless the edit itself spans notes);
   - merges as lookup+write+expert;
   - clarify prompts phrased with "accordingly" or "what happened".
2. **The regression check of Laya's other question sets** with the chosen checkpoint, which is now the main blocker before any use in Nuru.
3. **Shadow mode in Nuru:** log Laya's categories on real turns without acting on them.
