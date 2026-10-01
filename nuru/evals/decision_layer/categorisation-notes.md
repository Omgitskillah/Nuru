# Prompt categorisation: Laya against the 3B

Branch `prompt-categorisation`, started 2026-10-01. Eval 8 in the [README](README.md).

## Current state (2026-10-01)

- **The best sorter is the 3B at `low`:** 31/50 exact sets, 38/50 first pick right, 1.7 s per prompt. A vote of 5 calls raises that to 35 / 41, at about 8.5 s.
- **Laya's best format is 7 yes/no questions in one call:** 19 exact, 32 first right, 0.22 s. That beats its choice question (15 / 23) and the 3-call loop (0 / 23). It gets `expert` right on all 6 prompts, but still overuses `clarify` and almost never gives a second label.
- **Both contenders are weak on `clarify` and `expert`**, which are the categories routing depends on most.
- **As shipped, Laya can't be calibrated for this task.** It ranks wrong answers with high confidence (the mutex question went to `clarify` at 0.93), so fix the question format first, then fine-tune, and only then fit thresholds.
- **What this means for the step check sequence** (router → tools/evidence → 3B draft → check):
  - The 3B at `low` can route clear requests now, and its categories map onto tool subsets for tool narrowing.
  - The ordered label set (lookup → write, sweep → calculate) can serve as plan-then-act's checklist.
  - Laya is cheap enough to give a second opinion every turn.
  - Anything flagged `expert`, `clarify` or uncertain should take the safe path: all tools, or ask the user.

### Next steps, cheapest first

1. **Combine the two on paper:** take the 3B at `low`'s labels, plus `expert`/`clarify` when Laya's yes/no says so. This can be scored from the saved runs with no model calls.
2. **Re-ask `tool_gate_start`'s `mode`** (12-13% live) as yes/no questions, and replay it on the labelled decisions with `replay.py`.
3. **Train Laya on the yes/no question:**
   1. Freeze the 7 statements.
   2. Generate about 1-3k varied requests from dummyVault, weighted towards history-dependent and general-knowledge ones.
   3. Label them with soft targets, using the expert via the Batch API or a 5-call 3B vote, and check the teacher against about 100 hand labels.
   4. Fine-tune locally with `proper_reward` plus soft cross-entropy and balanced categories.
   5. Fit temperatures on a held-out 20%.
   6. Test on these 50 prompts (never trained on), plus a fresh 50.
   7. Check that gate_write and rerank haven't got worse.
   8. Fit thresholds with `calibrate.py`.

   First slice: 300 requests labelled by the 3B vote, and one short training run.
4. **After the tools purge**, prototype the router with tool narrowing, and benchmark it on the suite against 133.4/170.
5. **Still open:** "What does SPI stand for?" (`answer` or `lookup`?). The 50 prompts have no conversation history, so `clarify` behaviour in real use is still untested.

## Goal

Find out how well each contender sorts prompts into kinds of work, to support tool narrowing and routing. Laya is the fast decision layer, and granite4.2:3b is Nuru's local model. The prompts are only sorted, never answered.

## Categories

The definitions below are given word for word to both contenders.

| Category | Definition |
|---|---|
| `answer` | Reply directly; the user's notes aren't needed |
| `lookup` | Find one specific note or fact in the user's notes |
| `sweep` | Look across many notes: list, audit, compare or summarise a set |
| `calculate` | Arithmetic or statistics, on numbers from the notes or the request |
| `write` | Create, add to or change a note |
| `expert` | Deeper reasoning or planning than a small local model can do |
| `clarify` | Too unclear to act on; ask the user |

## The set

There are 50 prompts in [prompt_categories.py](prompt_categories.py):
- 6 were written per category. Since the relabel below, 39 prompts have one label and 11 have two.
- The last 8 are borderline and need two categories each: lookup+calculate, sweep+write, lookup+expert, sweep+calculate, lookup+write, write+clarify, sweep+expert and answer+write.

**Relabelled on 2026-10-01 (the user's decision):** a write to a note that already exists needs `lookup` too. So the Priya, reading-list and area-monitor write prompts are now lookup+write. That leaves 3 write-only prompts (a new note, a new transcript, and today's daily note, which is found by date), and 11 prompts with two labels. The script now picks out the multi-label prompts by label count instead of position.

Prompts are labelled by what they need, not by precedence. For example, a calculation that needs numbers from the notes is lookup+calculate, so the single-label `calculate` prompts carry their numbers in the request. Likewise, the single-label `expert` prompts don't need the vault.

## Method

The script is [categorise.py](categorise.py).
- **Laya:** an ad-hoc `category` choice question on the English checkpoint, with the state `{"request": ...}`. It is asked 3 times, and the pick from each call is removed from the next call's options. Laya's time is the time for the whole loop, and each pick's cumulative time is also logged.
- **Laya yes/no** (added later on 2026-10-01): one call with 7 yes/no questions, "Handling the user's `request` needs this: <definition>". Each category above 0.5 becomes a label (or the likeliest one if none clears 0.5), ordered most likely first. All 7 probabilities are saved.
- **3B:** `granite4.2:3b` with no Nuru system prompt and no tools, in one call. A forced JSON schema returns 1-3 categories, most important first. The default thinking level is `low` (Nuru's default), and `--think off` is available.
- **Order:** Laya runs first and the 3B second, so they never share the GPU. Each is warmed up first, untimed.
- **Scores:** each prompt is scored on these measures:
  - exact set match;
  - first pick right (the first pick is one of the gold labels);
  - recall (the share of gold labels found);
  - precision (the share of picks that are right).

  Both models are scored on all 50 prompts and on the multi-label ones alone (the 8 borderline prompts at first, and all 11 after the relabel). Laya is scored after 1, 2 and 3 calls.

## Watch points and possible improvements

These are kept up to date as the work goes on.

1. **Laya always gives 3 picks, but the gold sets have only 1-2 labels.** This caps Laya's precision at 0.33-0.67 and makes an exact match impossible after 3 calls. The 3B can stop at 1. So compare Laya at each k, and consider a stop rule, such as stopping when a pick's probability falls below some threshold. The threshold would need to be fitted on this set (an overfit risk with 50 prompts) or on held-out prompts.
2. **A single call may be enough.** Laya's first call already returns probabilities over all 7 options. The script also scores that call's top 2 and top 3, in that one call's time. If they match the loop's scores, the loop can be dropped and Laya becomes about 3x faster.
3. **Later calls are forced choices.** Once the right categories are removed, Laya still has to pick something, and the low probability of that pick is the only sign that nothing fits.
4. **The 3B's output varies.** It samples at the Modelfile's settings, so a single run is noisy. Laya is deterministic. If the scores are close, repeat the 3B runs.
5. **The comparison can't fully match.** Laya gets one `instructions` line and the option definitions. The 3B gets a system prompt containing the same definitions plus "don't answer, most important first". The wording differs a little because the formats differ.
6. **Laya's token budget:** 7 options plus a request must fit the English checkpoint's 512-token context. The predictor raises `OverBudget` rather than truncating. *Checked in the 2026-10-01 runs: every prompt fitted.*
7. **Laya is biased towards `clarify`, and it can't be calibrated yet.** Its first pick was `clarify` on 16 of the 50 prompts, including the mutex question at p=0.93. The checkpoint warns that its temperatures are invalid ("treat confidence as uncalibrated"). This general checkpoint can't do this task without training. Labelled prompts like these 50 are the kind of data Laya's fine-tuning needs.
8. **Some gold labels were debatable.** Resolved 2026-10-01: a write to an existing note is now lookup+write (see The set). One case is still open: "What does SPI stand for?" could be a lookup if the user's notes define it, and the 3B at `low` picks lookup for it 5 times in 10.
9. **The 3B never asks the user.** At `low` it picked `clarify` for "What about him?" and "Change it to the other one", but for "Do it again", "Make it better" and "Can you update the note?" it guessed real work. It never added `clarify` as a second label ("Add a note about it."). Prompts with no history are the 3B's blind spot.
10. **`expert` depends on the thinking level.** At `off` the 3B picked `expert` first on 5.1 of 6 expert prompts, but also put it on simple questions (the mutex question, 6 of 8 runs). At `low` it rarely picked `expert` (3.0 of 6), and tended to turn design tasks into lookup/sweep/write. Neither level is reliable for `expert`.
11. **`off` pads with extra labels.** At `off` the 3B often lists 2-3 categories when one fits, adding sweep about 15 times per run and lookup about 12 times. That halves exact matches (16 vs 30) at much the same first-pick accuracy. If the 3B is used, `low` is the better setting. Alternatively, ask for one label at `off` and a second only if needed.
12. **A run-to-run spread of ±3 exact matches** at `low` means a difference smaller than about 4 between two prompt or label variants isn't meaningful on a single run.
13. **Laya's yes/no version almost never gives a second label.** It averages 1.08 labels per prompt at the 0.5 cut-off. On the 11 two-label prompts it got the first pick right every time with precision 1.00, but no exact matches. Lowering the cut-off doesn't help, because the extra labels it adds are wrong (0.3: 18 exact, precision 0.61; 0.2: 10 exact). In practice it is a better single-label sorter, not a multi-label one.
14. **The yes/no version still overuses `clarify`** (14 first picks, including 5 of 6 `answer` prompts and two `calculate` ones). But it gets `expert` right on all 6 prompts, which no other contender does.
15. **Seven yes/no questions cost about 3 choice calls:** 0.22 s per prompt, the same as the 3-call loop. That's still about 8x faster than one 3B call at `low`.

## Results

### 2026-10-01: 2 full runs, plus 3B-only runs at `low` and `off`

There were 18 runs in all: Laya ran twice (with identical picks both times), the 3B ran 10 times at `low` and 8 times at `off`. The batch took 23 minutes. The 3B figures are means ± SD across runs; Laya's are exact.

**All 50 prompts:**

| Contender | Exact set | First pick right | Recall | Precision | Median per prompt | Per run (50) |
|---|---|---|---|---|---|---|
| Laya, 1 call | 18 | 23 | 0.41 | 0.46 | 0.08 s | 4 s |
| Laya, 2-call loop | 2 | 23 | 0.65 | 0.37 | 0.16 s | 8 s |
| Laya, 3-call loop | 0 | 23 | 0.79 | 0.31 | 0.23 s | 11 s |
| Laya, 1 call, top 3 | 0 | 23 | 0.75 | 0.29 | 0.08 s | 4 s |
| 3B, `low` (10 runs) | **30.2 ± 3.0** | **36.1 ± 2.7** | 0.78 ± 0.03 | **0.74 ± 0.04** | 1.70 s | 100 s |
| 3B, `off` (8 runs) | 16.3 ± 2.5 | 33.3 ± 1.0 | 0.73 ± 0.03 | 0.54 ± 0.02 | 0.76 s | 39 s |

**The 8 borderline prompts:**

| Contender | Exact set | First pick right | Recall | Precision |
|---|---|---|---|---|
| Laya, 2-call loop | 2 | 5 | 0.56 | 0.56 |
| Laya, 3-call loop | 0 | 5 | 0.81 | 0.54 |
| 3B, `low` | 2.7 ± 1.1 | 6.5 ± 1.0 | 0.66 ± 0.07 | 0.79 ± 0.07 |
| 3B, `off` | 3.3 ± 0.7 | 6.6 ± 0.9 | 0.79 ± 0.04 | 0.82 ± 0.07 |

**First pick right per category, single-label prompts (out of 6):**

| | answer | lookup | sweep | calculate | write | expert | clarify |
|---|---|---|---|---|---|---|---|
| Laya | 0 | 1 | 3 | 3 | **6** | 2 | 3 |
| 3B `low` (mean) | 3.1 | 5.5 | **6.0** | **6.0** | 3.2 | 3.0 | 2.8 |
| 3B `off` (mean) | 0.2 | 4.5 | **6.0** | **6.0** | 3.2 | 5.1 | 1.5 |

The 3B's `write` score counts `lookup, write` as a miss on the first pick (see watch point 8).

### The same runs, rescored with existing-note writes as lookup+write

There were no new model calls. Only the labels changed.

| Contender | Exact (of 50) | First pick right | Recall | Precision |
|---|---|---|---|---|
| Laya, 1 call | 15 | 23 | 0.38 | 0.46 |
| Laya, 3-call loop | 0 | 23 | 0.77 | 0.31 |
| 3B, `low` (10 runs) | **31.2 ± 2.8** | **38.0 ± 2.5** | 0.77 ± 0.03 | **0.76 ± 0.04** |
| 3B, `off` (8 runs) | 16.4 ± 2.5 | 33.9 ± 1.4 | 0.71 ± 0.03 | 0.55 ± 0.02 |

On the 11 prompts with two labels:

| Contender | Exact | First pick right | Recall | Precision |
|---|---|---|---|---|
| Laya, 2-call loop | 3 | 8 | 0.59 | 0.59 |
| Laya, 3-call loop | 0 | 8 | 0.77 | 0.52 |
| 3B, `low` | 4.6 ± 1.1 | 9.4 ± 1.0 | 0.70 ± 0.05 | 0.85 ± 0.05 |
| 3B, `off` | 4.0 ± 1.2 | 8.6 ± 1.2 | 0.75 ± 0.06 | 0.79 ± 0.07 |

The relabel helps the 3B at `low` a little (+1 exact, +2 first pick), costs Laya 3 exact matches, and leaves the gap between the two about the same.

**Conclusions:**
- The 3B at `low` sorts much better than Laya: 36 against 23 for the first pick, and 30 against 18 for exact sets. Laya is about 20x faster: 0.08 s per call against 1.7 s.
- Laya's loop raises recall (0.41 → 0.79), but at the cost of precision. It can never match a 1-2 label set exactly after 3 calls.
- A single call's top 3 recalls almost as much as the loop (0.75 against 0.79) in a third of the time.
- Laya is only reliable on `write`. That is likely because its `tool_gate_start` work is on similar wording, though the checkpoint is general and that hasn't been checked.
- Both contenders are weak on `clarify` and `expert`, which are the categories that matter most for routing.
- Raw results are in `.runs/categorise-20261001-1254*.json` to `-131547.json`.

### 2026-10-01 (later): Laya yes/no, one call with 7 yes/no questions

There was one Laya-only run: `.runs/categorise-20261001-143921.json`. The loop's picks were identical to the earlier runs.

**All contenders ranked by exact matches, then first pick right** (all 50 prompts, current labels):

| Rank | Contender | Exact (of 50) | First pick right | Precision | Time per prompt | Time per run (50) |
|---|---|---|---|---|---|---|
| 1 | 3B `low`, vote of 5* | 35.4 | 40.9 | 0.83 | about 8.5 s | about 500 s |
| 2 | 3B `low`, vote of 3* | 33.8 | 39.8 | 0.80 | about 5.1 s | about 300 s |
| 3 | 3B `low`, 1 call | 31.2 ± 2.8 | 38.0 ± 2.5 | 0.76 | 1.70 s | 100 s |
| 4 | 3B `off`, vote of 5* | 21.6 | 34.6 | 0.63 | about 3.8 s | about 195 s |
| 5 | 3B `off`, vote of 3* | 19.9 | 34.5 | 0.60 | about 2.3 s | about 117 s |
| 6 | **Laya yes/no, 1 call** | **19** | **32** | **0.63** | **0.22 s** | **11 s** |
| 7 | 3B `off`, 1 call | 16.4 ± 2.5 | 33.9 ± 1.4 | 0.55 | 0.76 s | 39 s |
| 8 | Laya choice, 1 call | 15 | 23 | 0.46 | 0.07 s | 4 s |
| 9 | Laya choice, 2-call loop | 3 | 23 | 0.38 | 0.14 s | 7 s |
| 10 | Laya choice, 1 call top 2 | 1 | 23 | 0.35 | 0.07 s | 4 s |
| 11 | Laya choice, 3-call loop | 0 | 23 | 0.31 | 0.21 s | 11 s |
| 12 | Laya choice, 1 call top 3 | 0 | 23 | 0.29 | 0.07 s | 4 s |

\* The vote rows are simulated from the saved 3B runs: a label is kept if more than half of the voting runs gave it, averaged over 200 random draws of 3 or 5 runs. The times assume the calls run one after another.

**Laya yes/no, first pick right per category (single-label prompts):** answer 1/6, lookup 3/6, sweep 4/6, calculate 3/6, write 2/3, **expert 6/6**, clarify 2/6.

**Conclusions:**
- Asking yes/no is Laya's best format so far. It gets 32 first picks right against 23 for the choice question, and 19 exact matches against 15, in the same time as the loop.
- The yes/no version is faster than every 3B setup and close to the 3B at `off` (19 exact against 16.4, 32 first picks against 33.9). It is still well behind the 3B at `low` (31.2 / 38.0).

