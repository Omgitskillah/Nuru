# Full-suite baseline after PR #5 (2026-10-06)

All 34 tests × 3 on plan-executor `61dff24` (PRs #2-#5 merged, test 32 scorer follow-up). Qwen3.5 4B,
thinking off, 16k context, nomic-embed-text-v2-moe, run_python with the rebuilt sandbox (source-line
tracebacks), expert off at the step cap. Batch `suite-full-qwen4b-post-pr5-20261006-172126`.

Compared with the 10-04 full run (`suite-full-qwen3.5-20261004-012255`), **rescored with today's
scorers** so both use the same rubric (the rescore leaves it at 132.7, as originally reported).

| | 10-04 | Now |
|---|---|---|
| Score (out of 170) | 132.7 | **139.0** |
| Runs scoring 4 or 5 | 72/102 | 77/102 |
| Median turn | 39.2 s | 39.0 s |
| p90 turn | 80 s | 140 s |
| Total turn time | 78 min | 111 min |

## Per test (points over 3 runs, out of 15)

| Test | 10-04 | Now | | Test | 10-04 | Now |
|---|---|---|---|---|---|---|
| 1 | 12 | 12 | | 18 | 15 | 14 |
| 2 | 14 | 14 | | 19 | 15 | 15 |
| 3 | 15 | 15 | | 20 | 8 | **5** |
| 4 | 15 | 15 | | 21 | 5 | 7 |
| 5 | 15 | 15 | | 22 | 12 | 10 |
| 6 | 9 | **12** | | 23 | 15 | 15 |
| 7 | 14 | 15 | | 24 | 15 | 15 |
| 8 | 12 | 13 | | 25 | 6 | **12** |
| 9 | 5 | **9** | | 26 | 15 | 15 |
| 10 | 15 | 15 | | 27 | 8 | 7 |
| 11 | 15 | 14 | | 28 | 15 | 15 |
| 12 | 11 | 13 | | 29 | 15 | 15 |
| 13 | 11 | 11 | | 30 | 12 | 12 |
| 14 | 15 | 15 | | 31 | 6 | 8 |
| 15 | 14 | 14 | | 32 | 3 | **7** |
| 16 | 14 | 14 | | 33 | 9 | **3** |
| 17 | 3 | **11** | | 34 | 15 | 15 |

**Gains:** 17 (+8), 25 (+6, drafting rule), 9 (+4, run_python), 32 (+4, final answer at the step limit),
6 (+3). **Losses:** 33 (-6), 20 (-3), 22 (-2). With ~±1 point of noise per test per run, only 33 is
clearly a regression; 20 and 22 need more runs to say.

## Test 33: the expert is no longer a model tool

Test 33 asks Nuru to attach two notes "to the expert" and have it check a schedulability analysis.
`ask_an_expert` was removed from the model's tools on 2026-10-05 (65ba289), so the test can no longer pass
as written. Worse, in all three runs the model treated "the expert" as a person note: it appended
`[[FreeRTOS Task Management]]` and `[[Architecture Review Meeting]]` links to
`nuru/People/bob-martinez.md` and claimed it had attached them to "your expert (Bob Martinez)". The
right reply now is to say the expert isn't available and answer from the notes, without writing.
Decide: bring the expert back as a tool, or rewrite test 33's rubric to expect that reply.

## Where the extra time went (+33 min)

1. **Final answers without tools re-read the whole prompt: 13 calls, 18 min.** When recovery runs out
   (PR #5) or the step limit is hit, the final answer is asked for with no tools. The tool definitions sit
   at the start of the prompt, so dropping them invalidates Ollama's cache and the whole ~7,000-token
   prompt is read again (40-77 s per call; test 16 shows it: two failed run_python calls, then a 64-77 s
   final answer). Keeping the tool list unchanged on those calls and refusing any tool call in code, as
   PR #5 already does for recovery, would avoid it.
2. **One runaway reply: 7 min.** On test 9 one call wrote 8,192 tokens (the output limit, stop reason
   `length`) and scored 0. A lower output cap for ordinary turns would bound this.
3. **Test 33 wandering:** 744 s against 282 s, searching and writing instead of asking the expert.
4. **Test 32:** 862 s against 285 s; longer turns that now end in a final answer (and include item 1).

The 10-04 run did more of its work through `ask_an_expert` (23 expert calls), which was quick for Nuru;
this run had none.

## Also seen

- Test 16 (gym log): the model calls `statistics(numbers=...)` inside run_python, as if the MCP tool were a
  Python function, and assumes a ```` ```csv ```` block that the note doesn't have. It still scores 13-15.
- Every turn's first call re-reads ~1,000 cached tokens (Ollama restores the nearest checkpoint of the
  hybrid model's state, at 5,130 of a 6,118-token shared prefix): ~5.8 s per turn. See
  `search-speed-feedback-2026-10-06.md`.

## Suggested next steps

1. Keep tool definitions unchanged on final-answer calls (refuse tool calls in code): ~18 min of this run.
2. Cap output tokens for ordinary replies (e.g. 2,048) so a runaway can't take 7 minutes.
3. Decide test 33: expert back as a tool, or a rubric that expects "the expert isn't available".
4. Fix the turn-start cache miss (checkpoint at the end of the shared prefix): ~5 s per turn.
