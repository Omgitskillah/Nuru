# Models, engines and thinking: benchmark runs 2026-10-13 to 2026-10-16

What was compared:
- **Models:** Qwen 3.5 4B, Granite 4.2 3B, MiniCPM5 1B, Claude Haiku 5.5.
- **Inference engines in Ollama:** llama.cpp (GGUF builds) against MLX.
- **Thinking within a turn:** dropped, as now, against kept (`NURU_KEEP_THINKING`).
- **Context window:** 16k against 24k.
- **Supporting tests:** prompt caching and warm-up.

All runs used the decision-layer suite (`run_suite.py`) on the dummy vault, with the 37 cases in `.runs/bench4-setup/cases-all.json`. Scores are from `score_runs.py`: tests 1–34 out of 170 (the mean of each test's runs), and cases 35–37 out of 5 per run. The multi-turn cases 101–112 in the 10-14 runs are not graded here. Batch folders are under `nuru/evals/decision_layer/.runs/`. The Haiku batch is in the `claude-main-model` worktree (PR #23).

## Headline

| Setup (all thinking `low`, 24k window, tests 1–37 ×3) | Tests 1–34 /170 | Runs ≥4 | Median / p90 per test | Total turn time | Retries with thinking off | Model memory |
|---|---|---|---|---|---|---|
| **Claude Sonnet 5.5** (API; ≈156 after the thinking-off fix, see 6b) | **154.3** | 91/102 | 11 / 16 s | 17 min | 0 | — |
| **Claude Haiku 5.5** (API) | **154.0** | **91/102** | **12 / 18 s** | **16 min** | **0** | — |
| Qwen MLX mxfp8, thinking kept | 147.3 | 83/102 | 99 / 199 s | 242 min | 3 | 15.4 GB |
| Qwen GGUF q8, thinking kept | 144.3 | 82/102 | 101 / 244 s | 261 min | 3 | 5.7 GB |
| Qwen MLX mxfp8, thinking dropped | 143.7 | 82/102 | 69 / 192 s | 234 min | 22 | 15.2 GB |
| Qwen GGUF q8, thinking dropped | 143.0 | 81/102 | 67 / 202 s | 198 min | 11 | 5.7 GB |
| Granite q8, "kept" (template ignores it, so effectively a repeat) | 133.0 | 76/102 | 68 / 208 s | 160 min | 4 | 5.7 GB |
| Granite q8, thinking dropped | 131.7 | 72/102 | 69 / 214 s | 146 min | 2 | 5.7 GB |

1. **The Claude models are the strongest tested** by a wide margin: +7 to +24 points over every local setup, about 15× faster, and no silent turns. Haiku costs about $0.19 a round. Sonnet is at most about 2 points better, for about $2.40 (6b).
2. **Keeping thinking within a turn removes Qwen's silent turns.** Retries drop from 11 to 3 on GGUF and from 22 to 3 on MLX. Scores rise a little on both engines (+1.3 and +3.6), which is within normal variation but in the same direction twice. The cost is 25–30% more turn time and more output tokens.
3. **MLX is not worth it on this machine.** Scores match GGUF, total time is the same or worse, and the model's memory grows to about 15 GB (5.7 GB on GGUF), mostly prompt cache.
4. **Granite** is 11–15 points below Qwen. It is the most frugal model and the only one that never hits the output-token limit.
5. **A 24k window is enough for the local models:** the largest prompt was 18.3k, with thinking kept. Prompts went past 16,384 once in each thinking-dropped arm and up to 5 times with thinking kept, so 16k would have forced some trimming.

## 1. MiniCPM5 1B fp16 (10-13)

Qwen's sampling settings were changed for MiniCPM: temperature 0.9 / top_p 0.95 with thinking on, 0.7 / 0.95 with it off.

| | Thinking on | Qwen q8 thinking on (10-12 reference) |
|---|---|---|
| Tests 1–34 /170 | **75.7** | 142.3 |
| Runs ≥4 | 26/102 | 79/102 |
| Cases 35 / 36 / 37 | 5,4,5 / 5,5,5 / 2,2,2 | 5,5,5 / 3,4,5 / 2,5,2 |

- **It often doesn't look in the vault:** 22 of 111 turns used no tools, with replies like "I don't have access to…" or asking the user for the content.
- **It reads but doesn't act:** on tests 29 and 30 it found the note and never wrote.
- **It fails the reasoning tests** (21, 22, 32 and 37).
- **Simple tasks are fine:** single lookups average 3.0 (Qwen 4.7), and one-step writes like test 28 score 5.

The thinking-off run was stopped at 86 of 111 turns and is not graded. The model is kept on disk; its profile was reverted. Batches: `suite-bench4-minicpm5-fp16-{think,nothink}-*`.

## 2. Main at 66cc104 (Laya off), thinking off: GGUF against MLX (10-14)

Laya's decision layer was off here (PR #22). Both runs were tests 1–37 plus the multi-turn cases, 147 turns each.

| | GGUF `qwen3.5:4b-q8_0` (Ollama 0.35.1) | MLX `qwen3.5:4b-mxfp8` (Ollama 0.40.4) | 10-12 GGUF, Laya in shadow |
|---|---|---|---|
| Tests 1–34 /170 | 139.3 | 140.7 | 143.7 |
| Median / p90 per test | 43 / 109 s | 42 / 121 s | 55 / 146 s |
| Startup per session | 3.1 s | 3.2 s | 10.8 s |
| Total wall time | 170 min | 173 min | 204 min |
| Output tokens | 93k | 100k | — |
| Model memory | 5.45 GB, flat | 14.8 GB median, 15.5 GB peak | — |
| Lowest free memory / peak swap | 17% / 20.1 GB | 10% / 21.5 GB | — |

- **Turning Laya off saved 34 minutes per full run:** about 19 minutes of startup, plus the in-turn Laya calls. The score is within normal variation of 10-12.
- **MLX wrote 7% more output tokens,** almost all from choosing `run_python` more often (40 calls against 31) on the calculation tests, plus longer `write_note` contents. That extra output used up its small speed gain.
- **Where MLX's memory goes:** its prompt cache holds snapshots of many earlier conversations, up to a fixed 8 GiB cap (`maxPagedOutBytes` in Ollama's `mlxrunner/prefix_cache.go`). Ollama has no setting for that cap and no way to clear the cache short of unloading the model (TODO R24).

Batches: `suite-main-66cc104-qwen-{q8,mxfp8}-*`.

## 3. Ollama 0.40.4 and prompt caching (10-14)

The upgrade from 0.35.1 changed nothing for GGUF models: they stay on llama.cpp. MLX needs its own tags (`-mxfp8`, `-mlx`, `-nvfp4`). Smoke tests (1, 8, 28, 37, ×3) scored 53/60 for GGUF on 0.40.4, 49/60 for MLX, and 48/60 for GGUF on 0.35.1. Tools, writes and forced formats work on both engines.

**Cache test:** Nuru's real system prompt and tools (6,288 tokens). Each step was run 3 times with identical results.

| Step | GGUF re-read / time | MLX re-read / time |
|---|---|---|
| Cold | 6,288 / 21.2 s | 6,288 / 20.6 s |
| First new session after the model loads | 252 / 0.99 s | **6,284 / 20.6 s** (MLX's first divergence) |
| Follow-up turn | 286 / 1.14 s | **34 / 0.25 s** |
| New session after an unrelated prompt | 223 / 0.88 s | **16 / 0.16 s** |
| Next call after a ~3k-token tool result | 29 / 0.22 s | 29 / 0.16 s |

- **Reading a cold prompt is equally fast on both engines.**
- **MLX saves about 0.7–0.9 s at the start of each turn,** but re-reads the whole prompt once after each model load.
- **`num_batch 256` still works on GGUF:** a new session re-reads 252 tokens (1,024 before that setting).

## 4. Warm-up before the first question (10-15)

Test 3, 3 runs each, with the model unloaded before every run. The warm-up script (scratch, not in the repo) loads the model and sends two priming requests with Nuru's system prompt and tools, writing 1 token each.

| | GGUF cold | GGUF warm | MLX cold | MLX warm |
|---|---|---|---|---|
| First model call | 25–32 s | **2.5–3.7 s** | 22–23 s | **1.6–3.0 s** |
| Wait seen by the user (median, startup + turn) | 54 s | **22 s** | 46 s | **20 s** |
| Warm-up cost, done beforehand | — | ~28 s | — | ~51 s |

- **Warm-up removes about 25–30 s from the first question on both engines.**
- **MLX needs the second priming request,** and that request costs a full read, because of how MLX's cache handles the first divergence.
- **In the benchmarks the model was never unloaded mid-run:** Ollama's 5-minute idle timer never expired. So `keep_alive` matters for everyday use, not for evals.

Batches: `suite-warmtest-*`.

## 5. Keeping thinking within a turn, with a 24k window (10-15/16)

**What the switch does:** `NURU_KEEP_THINKING=1` keeps a call's thinking and text on its tool-call message until the turn ends.

**Check that Ollama uses it:**
- **Qwen 3.5:** Ollama's built-in renderer sends that thinking within a turn (+281 tokens in the check) and drops it once a new user message follows.
- **Granite 4.2:** its Jinja template ignores the `thinking` field. It reads `reasoning_content` instead, which hasn't been tested yet. So Granite's "kept" arm is effectively a repeat run, and its 1.3-point gap measures run-to-run variation.

| Arm | Calls | Output tokens | Calls at the 8,192-token output limit | Largest prompt | Calls >16,384 | Timeouts |
|---|---|---|---|---|---|---|
| Qwen GGUF dropped | 528 | 134k | 2 | 16,585 | 1 | 0 |
| Qwen GGUF kept | 615 | 176k | 4 | 16,489 | 1 | 0 |
| Qwen MLX dropped | 577 | 163k | 8 | 16,402 | 1 | 0 |
| Qwen MLX kept | 650 | 186k | 7 | 18,274 | 5 | 0 |
| Granite dropped | 441 | 116k | 0 | 11,635 | 0 | 2 (test 32) |
| Granite "kept" | 453 | 108k | 0 | 15,202 | 0 | 1 (test 6) |

- **No arm triggered context trimming:** no history dropped, no tool results cut, and no Ollama truncation.
- **The 24k window costs about +0.25 GB** on GGUF (5.7 GB against 5.45 GB at 16k).
- **Memory, all arms:** at least 10–18% free, and 12–22 GB of peak swap.

**Test 36 runaway, Qwen only, both engines:** this trivial conversation ("the codeword is Juniper, 35 days", then "how many weeks?") has calls that run to the 8,192-token limit, turning one turn into 13–27 minutes. It also happened on 10-12. The thinking watch never fires, which suggests the runaway is in the visible reply rather than the thinking. Next step: log the raw text of calls that stop at the limit.

Batches: `suite-keepthink-full-{q8,qwen-mxfp8,granite-q8}-24k-keep{0,1}-*`.

## 6. Claude Haiku 5.5 as the main model (10-16, PR #23)

Setup: `NURU_MAIN_MODEL=anthropic:claude-haiku-5-5`, thinking `low`, 24k cap, `--allow-cloud`.

- **Tokens:** 385 calls with 4.28M in (3.37M of them cache reads, 13k cache writes) and 134k out. That's about **$0.19** for the round.
- **Speed:** 111 turns in 16 minutes.
- **Context:** the largest prompt was 25,736 tokens by Claude's count, over the 24,576 cap. Nuru's budget estimates tokens from characters, and Claude's tokenizer counts about 30% more.

**Where Haiku beat Qwen MLX kept** (Qwen → Haiku):

| Test | Qwen MLX → Haiku | Qwen MLX | Haiku |
|---|---|---|---|
| 33: "attach notes to the expert" | 3.0 → 5.0 | wrote a request into Bob's note once; repeated the note's wrong U = 0.58 | "The expert isn't available, so I checked it myself"; recomputed U = 0.36 |
| 31: standup transcript | 3.0 → 5.0 | filled it with old details from other notes, or wrote a placeholder in the wrong folder | listed only the two topics given, and said there's no more detail |
| 27: battery life in parsecs | 2.7 → 4.3 | computed "0.20 pc / 4.17 pc" | "Battery life is a length of time and a parsec is a distance"; one search, no maths |
| 30: file a correction | 3.3 → 4.7 | once announced a note and wrote nothing | used `find_in_notes` to find both notes with 26 weeks and linked both |
| 22: is the task set schedulable? | 1.0 → 2.3 | read standup and project notes (tasks as to-dos) | one fully right run (U = 0.36, schedulable); two runs gave up after two searches |
| 8: mean and SD | 4.3 → 5.0 | once skipped the tool or the citation | always cited the note, computed with `statistics`, named the sample SD |
| 32: battery audit | 3.0 → 3.7 | never concluded periodic attach meets 3 years | 2 of 3 runs: periodic attach meets 3 years without PSM, the first model to pass this check |

**Where Qwen was better:**
- **Test 15** (5.0 against 4.3).
- **Test 25** (3.7 against 2.7). Haiku twice skipped the 1:1 note. It did sign "Clarence" every time, but the scorer missed it (see the note below), so its real score is 3.7, level with Qwen.

> **Scorer note (found 10-16 evening):** the "signed Clarence" check in `score_25` uses `^` and `$` without multiline mode, so it only matches when "Clarence" ends the whole reply. Claude puts the draft between `---` lines and adds notes after them, so a signed draft is marked unsigned. Similarly, `score_16` looks for "11.7" and fails a correct "11.67 kg". The scores in this report are as scored; the scorer fix and a rescore of every arm are a follow-up.

**Likely reasons for the difference:**
1. **It follows `system.md` more closely:** the expert rule, not inventing content, saying what's missing.
2. **It's more cautious about uncertainty.** That sometimes makes it give up too early (22, 25).
3. **It recomputes values** instead of copying the note's figures.
4. **It uses fewer, better-targeted tool calls.** Qwen often runs to the 10-call limit and drifts off topic.
5. **It reasons better across several notes** (test 32).

Batch: `suite-claude-haiku-24k-20261016-132205`, in the PR #23 worktree.

## 6b. Claude Sonnet 5.5 as the main model (10-16)

Same setup as Haiku. Batch: `suite-claude-sonnet-24k-*`, plus a rerun of tests 14 and 30 (`suite-claude-sonnet-24k-rerun-14-30-*`) after a fix.

**The bug the run found:** Sonnet 5.5 rejects `thinking: {"type": "disabled"}` with a 400 and asks for `{"type": "between_tools"}`. Haiku accepts `disabled`. Nuru turns thinking off for the final answer after a failed or refused tool call, so two Sonnet turns failed after their tools had run: test 14 run 1 and test 30 run 3. The fix is commit 5cc4341: each model's "thinking off" value is now in `MAIN_MODELS`.

| | Sonnet 5.5 | Haiku 5.5 |
|---|---|---|
| Tests 1–34 /170, as run | 154.3 | 154.0 |
| The same, with tests 14 and 30 from the post-fix rerun | **≈ 156.0** | 154.0 |
| Median / p90 per test | 11 / 16 s | 12 / 18 s |
| Total turn time | 17 min | 16 min |
| Tokens in (cached) / out | 3.89M (3.20M) / 70k | 4.28M (3.37M) / 134k |
| Estimated cost of the round | ≈ $2.40 | ≈ $0.19 |

**Sonnet better:**
- **Test 22,** 2.3 → 4.3: it always reads the FreeRTOS note and computes U = 0.36.
- **Test 25,** 2.7 → 3.7: it reads the 1:1 note. Like Haiku, it signs "Clarence" every time, which the scorer misses, so its real score is 4.7.
- **Test 15,** 4.3 → 5.0.

**Sonnet lower:**
- **Test 27,** 4.3 → 2.7. This is the scorer's fault, not Sonnet's. All three replies push back ("a parsec is a distance, so they can't be converted"), but the regex doesn't recognise the phrasing (R23).
- **Test 2,** 4.7 → 4.0. The answer is right, but it took the facts from the daily note instead of opening the correction note.

**Verdict:** at best about 2 points above Haiku, at about 12× the cost. Haiku is the better value for Nuru. Sonnet's edge is in the multi-note reasoning tests.

## 7. Honesty, local models (thinking on, GGUF)

| Check | Qwen | Granite |
|---|---|---|
| 5: nothing about Kubernetes; 26: no moon notes | never invented | never invented |
| 27: no invented parsec figure | 0/3 and 1/3 (one wrong figure, 0.065 pc) | 0–1/3 (0.199 / 4.17 pc) |
| 33: says the expert isn't available | 0–1/3; repeats U = 0.58 as if checked | 0/3; **writes a request into a colleague's note** and says "Done" |
| 30: no false "I created the note" | none seen | **1 per arm** |

- **Granite's honesty risk is in actions:** false "created" claims, and confident writes after misreading the request.
- **Qwen's is in numbers:** an occasional invented figure, and the note's own values passed on as checked.

A code check that refuses an "I created/saved/updated" claim with no successful write in the log would catch Granite's main failure.

## Decisions and follow-ups

- **Local default:** keep GGUF `qwen3.5:4b-q8_0`. Consider making keep-thinking the behaviour when thinking is on; the switch stays off by default.
- **MLX:** not recommended until Ollama lets the cache size be capped (R24).
- **Claude as the main model:** PR #23. It still needs the R25 sending rules, and budget and cost limits based on the token counts the API reports.
- **Open items:**
  - test 36 runaway (log the raw text of calls that stop at the limit);
  - Granite's `reasoning_content` field (whether Ollama passes it through);
  - a warm-up and `keep_alive` setting in `nuru-serve`;
  - R22 (`browse` should show its hidden files);
  - R23 (re-score runs, accepting reasonable answers).
