# Live planner check, 2026-10-04

> **Historical record.** Plan mode was removed on 2026-10-24 (PR #37). The planner, executor, planner Modelfiles and `planner_check/` files this page links to no longer exist on main; they are at commit [7c2c345](https://github.com/Omgitskillah/Nuru/tree/7c2c3458657409a6be66fa88036c09f01cd7a500).

Does Nuru do better if Qwen3.5 4B first writes a plan, and the harness then carries it out with the same loaded model?

**Short answer: no.**
- **The planner is faster and uses about a tenth of the tokens,** but it's less accurate. On the full suite it scored 3.17/5 with 51/102 passes, against 3.90 and 72/102 for Nuru's own turn.
- **The main cause is plans that stop early.** 39 of 102 plans end after a search or pick, without reading the note or carrying out the request.
- **There were no illegal writes.**

## Setup

- **One model:** Nuru on `qwen3.5:4b` (`NURU_BASE_MODEL=qwen3.5:4b`), thinking off (Qwen's profile default).
- **The planner is a Modelfile swapped in, not a second model.** For each turn, [live_plan.py](decision_layer/live_plan.py) sends the request to Nuru's own model, `nuru`, with the planner Modelfile [qwen3.5-4b-planner.Modelfile](../modelfiles/planner/qwen3.5-4b-planner.Modelfile) applied to that one call:
  - the Modelfile's SYSTEM text goes as the system message, and replaces Nuru's prompt
  - its PARAMETERs go as options (temperature 0, seed 42, …)
  - `format` is set to the plan schema, with thinking off
- **Why the swap was needed:**
  - **A different model name forced reloads.** The first try used a separate `qwen35-planner:4b` model name. Nuru's own check looks for a model *named* `nuru`, so it reloaded the model twice per turn (once at the default 4096 context, then at 16384).
  - **The same name doesn't.** No planner call loaded anything (load time 0 s on all 102).
  - **What it still costs:** a swap re-reads the prompt that comes in, since Ollama keeps only the last prompt it processed.
- **Planner settings:** the same as the planner check, except `num_ctx`. The planner runs at Nuru's 16384 instead of the Modelfile's 4096, so the model can be shared.
- **Execution:** a valid plan is translated into the executor's step language and carried out by `Session.run_plan`. Tool steps go through the same gates and logs as a normal turn.
  - A `pick` that feeds a path argument stays a pick.
  - Any other `pick` (extracting values) becomes a typed `draft`.
  - A missing final answer step is added.
- **Fallback:** an empty or invalid plan leaves the turn to Nuru's own `ask()`, as agreed.
- **Runs:** [run_suite.py](decision_layer/run_suite.py) `--planner <Modelfile>`, fresh scratch dummyVault per turn.
  - One prompt (test 8).
  - Then the plan-vs-free set: 12 tests × 3 = 36 turns, `suite-planner36-qwen3.5-20261004-213654`.
  - Then the full suite: 34 tests × 3 = 102 turns, `suite-planner-full-qwen3.5-20261004-220010`.
- **Scoring:** `score_runs.py`; the comparison is `compare_arms.py`.
- **Baselines:** Qwen 4B's own turns from these batches:
  - `suite-qwen-free-20261003-221205` (the 36)
  - `suite-full-qwen3.5-20261004-012255` (the 102)
- **Caveat:** both baselines ran before commit `ed736f3` ("never escalate math"), which changed Nuru's prompt, so the comparison is close but not exact.

## Results

| | Own turn: score | Planner: score | Own turn: passes | Planner: passes | Own turn: median | Planner: median | Own turn: p90 | Planner: p90 | Tokens per turn (median), own / planner |
|---|---|---|---|---|---|---|---|---|---|
| 36 (12 tests × 3) | 4.86 | 4.11 | 36/36 | 27/36 | 28.4 s | 22.2 s | 51.9 s | 54.7 s | 20.4k / 3.3k |
| Full suite (34 × 3) | 3.90 | 3.17 | 72/102 | 51/102 | 39.2 s | 30.9 s | 81.8 s | 72.2 s | 29.5k / 3.3k |

**Planning itself** took a median 6.1 s per turn, out of the 30.9 s median turn.

**What happened to the 102 plans:**
- **90 were carried out.**
- **12 fell back to Nuru's own turn:** 9 empty plans and 3 invalid ones.
- **39 of the 90 carried out had no final answer step.** They stop after a search or a pick (tests 1, 2, 7, 9, 13, 19, 22, 28–33). Because the harness adds an answer, the reply is then written from whatever had run, often just search snippets.

### Where the planner wins and loses (full suite, mean score, own → planner)

**Better:**

| Test | Score | Why |
|---|---|---|
| 6 | 3.0 → 4.0 | |
| 8 | 4.0 → 5.0 | the read → extract → statistics chain works |
| 9 | 1.7 → 2.3 | |
| 32 | 1.0 → 3.3 | Nuru's own turn hits the step cap and the broken expert planner; the plan doesn't |
| 33 | 3.0 → 3.3 | |
| 3, 4, 14, 34 | same score | 1.5–2× faster |

**Worse**, each the same plan on all 3 repeats:

| Test | Score | What the plan did |
|---|---|---|
| 7 power budget | 4.7 → 1.0 | search → pick and stop; never reads or computes |
| 10 temperature coefficient | 5.0 → 2.0 | two searches, then answers from snippets |
| 12 Memfault and flash wear | 3.7 → 1.0 | two searches, answers from snippets |
| 18 supercap, me or Alice | 5.0 → 1.7 | picks "the notes" (one pick can only choose one), reads one |
| 19 expected battery life | 5.0 → 0.0 | search → pick and stop |
| 20 contradictions | 2.7 → 0.7 | not something a plan in this language can express |
| 21 open action items | 1.7 → 1.0 | `browse` with an invented `type: "tasks"` |
| 22 schedulable | 4.0 → 1.0 | search only |
| 29 Priya | 5.0 → 2.0 | picks the note's *content* into `supersedes`; the pick can't choose that, so the plan stops before writing |
| 30 file a correction | 4.0 → 1.0 | search → pick and stop; the correction is never written |

**Same failure on another set:** these are the plan failures from the planner check (stopping early, answering from search snippets, write fields like `supersedes`). They hold on a real vault.

**What if plans without an answer step fell back?** Suppose the harness had sent those 39 turns to Nuru's own turn instead, using the baseline's scores for them and adding the planning time. The result would be 3.6/5, 64 passes and a 41.7 s median. That's still below the own turn, and no faster.

### Writes

Every write and move on all 102 turns, plus the vault diff of every turn:

| Test | Request | What changed | Verdict |
|---|---|---|---|
| 28 (×3) | create today's daily note saying … | `nuru/daily-2026-10-04.md` created with the user's text | correct (5/5) |
| 31 (×3) | "Create a transcript of today's standup: …" | `nuru/daily-2026-10-04.md` created with "Today's standup: we discussed the soak test and the supercap." | wrong place (a daily note, not a transcript), but the content is the user's and nothing existing was touched; the own turn wrote nothing (2/5 both) |

- **No moves, deletes or replace-mode writes, and no existing note was changed.** Test 26 (delete moon notes) produced an empty plan three times, and Nuru's own turn then correctly deleted nothing.
- **Test 29's write never happened:** that plan stopped before it.
- **In the 36-turn batch,** only test 28 wrote, as requested.

## Resources

Sampled every ~0.5 s over the 107 minutes of the 36 + 102 runs. M4, 16 GB.
- **GPU:** above 90% busy in 72% of samples; the rest is tool calls and MCP startup.
- **GPU memory:** 4.9 GB median, 8.7 GB peak. That's the Qwen 4B at 16384 context plus `nomic-embed-text`; the peak is a moment both were loading.
- **CPU:** under 18%.
- **Free memory:** median 44%, low point 13%.
- **Swap grew from 8.2 GB to a peak of 18.3 GB** (16.3 GB at the end). Other apps were running too, so not all of it is Nuru, but memory was tight throughout.
- **Planning never loaded a model.**
- **Sleep:** the display slept partway through. Sampling shows no gap over 5 s, so the system didn't. `caffeinate` held the system awake for the rest of the run.

## Conclusion

- **A live Qwen 4B planner makes Nuru faster but less accurate.** Turns are 21% faster at the median, use a tenth of the tokens, and are 12% faster at p90. But 21 fewer turns of 102 pass.
- **The planner can't decompose open questions reliably:** "what's my battery life", "is my task set schedulable", "who's right". It stops after finding the note.
- **The executor itself works.** When a plan is complete (tests 3, 4, 8, 14, 24, 34), it's as accurate as the own turn, or better, and faster.
- **Swapping a planner Modelfile onto Nuru's loaded model is free** as long as the model name and `num_ctx` match. The remaining cost is re-reading the incoming prompt on each switch.
- **Options:**
  - **Keep Nuru's own turn** as the default.
  - **Use plans only where they're complete and simple:** single lookups, arithmetic, exact writes. That could be fixed templates as suggested yesterday, or this planner with a rule that only plans ending in an answer step after a read or tool step run.
  - **A better planner** (Qwen 4B with thinking on scored 92/100 in the planner check). It would cost about 7 s more per turn, which roughly cancels the speed gain.

## Added afterwards: Claude Haiku as the planner, on the hardest test

**The change:** the expert stops being a tool the model can call and becomes the planner.
- **Hidden from the model:** `ask_an_expert` was taken out of `MODEL_TOOLS` in [session.py](../src/core/session.py), so no model is offered it.
- **Modelfiles:** its section was removed from the [qwen3.5](../modelfiles/qwen3.5.Modelfile) and [granite4.2](../modelfiles/granite4.2.Modelfile) Modelfiles. That's the escalation rules, the expert clause in the opening line, "or escalate" in the retry rule, and the expert example. The math rule that sat inside the escalation section stays, as a short "MATH" section.
- **New planner tool:** deep-think gained `write_plan` (harness-only), on the same server as `ask_an_expert`.
  - Model: `claude-haiku-4-5` with thinking `{"type": "enabled", "budget_tokens": 1024}`. Haiku 4.5 takes no effort setting, and 1024 is the smallest budget the API accepts.
  - Prompt: [expert-planner.Modelfile](../modelfiles/planner/expert-planner.Modelfile). That's the Qwen planner prompt with `ask_an_expert` removed from the plan tools.
  - Wiring: `live_plan.make_expert` calls it through the session's MCP servers. `run_suite.py --planner expert` turns it on.
- **No forced schema:** structured outputs refuse the plan schema with "compiled grammar is too large", even with its regex patterns removed. So Haiku follows the prompt's step language, and the plan is checked locally against the schema without the expert step. A plan in a ```json fence is unwrapped.

**Test 17**, run 3 times. It's the lowest-scoring test across the recent full runs: 1.0/5 for Qwen alone, Qwen with the Qwen planner, and Granite. The prompt: "When do I have to cancel my internet contract, and how many days is that from today?"

| Repeat | Haiku's plan | What happened | Score | Turn time | Planner time |
|---|---|---|---|---|---|
| 0 | search "internet contract cancel" → pick note → read → answer | pick found no matching note, so the plan stopped. Reply: "nothing found" | 1/5 | 33.7 s | 8.0 s |
| 1 | search → pick "the cancellation deadline date" → answer | the pick became a draft from search snippets, which invented "2026-09-17", with "today (2026-09-18)" | 1/5 | 26.1 s | 7.9 s |
| 2 | search "internet contract cancellation" → pick → read → answer | as repeat 0 | 1/5 | 24.2 s | 7.4 s |

**Why it fails: the search, not the planner.**
- **The note is in German:** `nuru/berlin-admin-chaos-notes.md`, "Internet: Vertrag läuft bis 2027-03-31. Kündigungsfrist 3 Monate → spätestens bis 2026-12-31 kündigen".
- **English queries miss it.** Against the test vault, "internet contract cancel" and "internet contract cancellation" both leave it out of the top 8 results (daily notes and project notes, all scoring 0.52–0.56).
- **A German query finds it.** "Internet Vertrag Kündigung" returns it first, as an exact match.
- Two of Haiku's three plans were sensible (search → pick → read → answer). No plan can succeed while the search can't find the note.

**Two more gaps showed up:**
- **The executor doesn't know today's date.** Repeat 1's answer guessed "today (2026-09-18)". The answer and draft prompts need the date.
- **Date arithmetic.** "How many days from today" needs a date difference. The step language has no way to say that unless `calculate` takes dates. Not checked.

**Cost:**
- A Haiku plan took 7.4–8.0 s, against about 6 s for the Qwen planner on the same loaded model.
- Each call is one request: the ~2k-token prompt plus a short plan.
