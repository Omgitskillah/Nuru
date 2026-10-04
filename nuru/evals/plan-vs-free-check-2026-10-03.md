# Plan-then-act vs Nuru's own turn, 2026-10-03

Does Nuru get faster or more accurate if the harness carries out a plan, instead of Granite deciding every step itself?

**The two setups:**
- **A, Nuru as it is:** Granite 3B with thinking low; tools called as the model decides. This is the current `Session.ask`.
- **B, plan then act:** the same request with a hand-written plan. The harness runs every tool step through the same gates and logs as A (`Session.run_plan`, [executor.py](../src/core/executor.py)). Granite 3B (thinking low) only makes three kinds of small calls, never with tools offered:
  - `pick` a path, from a choice limited to the results' own paths
  - `draft` an expression, list or text, forced to the type the next step needs
  - write the `answer`

**Setup:**
- 12 of the 34 suite tests, 3 repeats each.
- Every turn ran on a fresh scratch copy of dummyVault ([run_suite.py](decision_layer/run_suite.py), `--plans` for B).
- Scored by [score_runs.py](decision_layer/score_runs.py) and compared with [compare_arms.py](decision_layer/compare_arms.py).
- I wrote the 12 plans ([decision_layer/plans/](decision_layer/plans/)) from the prompts and tool descriptions only, without looking at the vault; the user reviewed them before the run.
- **Rule decided before running:** proceed with the restructure if B's accuracy is at least A's and its median time, *including planning*, is 30% or more faster, or if B is 0.5/test or more more accurate at about the same speed.

## Results

| Test | A score | B score | A passes | B passes | A median | B median |
|---|---|---|---|---|---|---|
| 3 birthday + dentist | 5.0 | 5.0 | 3/3 | 3/3 | 55 s | 8 s |
| 4 September 17 | 4.0 | 4.7 | 2/3 | 3/3 | 33 s | 21 s |
| 7 average current, battery life | 4.0 | 4.0 | 3/3 | 3/3 | 168 s | 66 s |
| 8 mean/SD of 9 runs | 3.0 | **5.0** | 0/3 | 3/3 | 37 s | 15 s |
| 10 temperature coefficient | 5.0 | 4.3 | 3/3 | 3/3 | 48 s | 37 s |
| 14 backoff cap | 4.3 | 5.0 | 2/3 | 3/3 | 44 s | 36 s |
| 15 inventory value | 5.0 | 5.0 | 3/3 | 3/3 | 47 s | 62 s |
| 24 vendor email | 5.0 | 4.7 | 3/3 | 3/3 | 37 s | 13 s |
| 26 delete moon notes | 5.0 | 5.0 | 3/3 | 3/3 | 20 s | 2 s |
| 28 daily note | 5.0 | 5.0 | 3/3 | 3/3 | 8 s | 4 s |
| 29 Priya | 5.0 | 5.0 | 3/3 | 3/3 | 41 s | 8 s |
| 34 arithmetic | 5.0 | 5.0 | 3/3 | 3/3 | 12 s | 4 s |
| **All** | **4.61** | **4.81** | **31/36** | **36/36** | **38 s** (p90 95 s) | **19 s** (p90 63 s) |

B also used about 4.5x fewer tokens: a median of 4.5k per turn against 20.5k.

**Adding planning.** Qwen3 8B (`plan-qwen3`) wrote a plan for each of the 12 prompts in a median of 11 s; the first took 40 s while the model loaded. B's turn plus its plan:

| | Median | p90 | All 36 turns |
|---|---|---|---|
| A | 38 s | 95 s | 31 min |
| B, execution only | 19 s | 63 s | 16 min |
| B + Qwen3 planning | 31–33 s | 101 s | 25–26 min |

**B's first run, before four executor fixes, scored 4.25/5 (27/36).** Its failures were all wording in the executor's prompts, not the structure. These were fixed, then B was run again (the table above):
1. A `draft` was shown its inputs under step names, and wrote `30400 / s5`.
2. A `pick` saw only search snippets and chose "none", although the note was there.
3. The answer prompt said "report numbers exactly", giving 234.945 rather than 234.95, and never received its step's `want`.
4. The answers talked in step names.

## What it shows

- **Given a good plan, plan-then-act is better on every count.** It's more accurate (4.81 against 4.61; every test passed), twice as fast to execute, and uses a quarter of the tokens. The biggest gains came where Granite's own loop struggles:
  - test 8 went from 0/3 to 3/3: `statistics` got a list drafted from the note
  - test 7 dropped from 168 s to 66 s: no tool-calling loop
  - lookups took 8–13 s against 37–55 s
- **The plan is the weak link, in both speed and safety.**
  - With planning, the median gain is only 13–18%, under the 30% the rule asked for. The accuracy gain, +0.2/test, is under the +0.5 bar.
  - More importantly, Qwen3's own plans for these prompts are clearly worse than the hand-written ones. In 3 of 12 it would have gone wrong:
    - **Test 26** (delete moon notes): `search "moon"` → pick → `rename_or_move_file` to trash. `search` always returns matches by meaning, so this plan could trash an unrelated note.
    - **Test 7:** writes a note nobody asked for, and searches a second time for "resulting battery life".
    - **Test 10:** no vault lookup at all; it drafts an answer from general knowledge.
- **By the rule set before running, this isn't a go for a full planner-driven restructure.** The executor is a clear win; free-form planning by a local model is not yet safe or fast enough to put in front of it.

## Suggested next step

Use the executor without a free-form planner. Most requests in this set fit a handful of fixed plan *templates*:

| Kind of request | Template | Tests |
|---|---|---|
| look up | search → pick → read → answer | 3, 4, 24 |
| compute | … → draft → calculate / statistics → answer | 7, 8, 10, 14, 15, 34 |
| write | search → pick → write_note → answer, or write_note daily | 28, 29 |
| find | find_in_notes → answer | 26 |

The router already exists (`tool_gate`'s mode: read / write / answer); it would choose the template, with Granite filling only the `want`s and literal arguments. That keeps B's speed, needs no second model and no planning time, and can't invent a destructive step. Anything that fits no template falls back to Nuru's own turn.

This should be tested the same way before anything is restructured.

## Also fixed while running this

- **The scorer predated the tools purge.** It now knows `search`, `browse`, `tags` and `note_info`, and test 28 accepts the daily note in `nuru/`. Applied to both setups.
- **The Obsidian submodule writes `.obsidian-mcp-write.lock` into the vault on every turn.** The scorer counted it as a change, failing "nothing changed" in both setups. The scorer now ignores it, and `run_suite` skips it.
- **The tool-running code in `Session.ask` is now shared helpers** (`_before_tool`, `_write_blocked`, `_refuse_tool`, `_call_tool`), so B's writes face the same gates as A's.

Raw batches: `decision_layer/.runs/suite-free-20261003-194318/`, `suite-plan-20261003-202039/` (B before the fixes) and `suite-plan2-20261003-204839/`.

## Rerun on Qwen3.5 4B (same day, later)

The same 12 tests × 3 repeats, the same 12 plans, with `NURU_BASE_MODEL=qwen3.5:4b`. Qwen ran at its profile's default (thinking off), with the profile's sampling: temperature 0.7, top_p 0.8, no presence penalty ([profiles.yaml](../modelfiles/profiles.yaml)).

| | Mean score | Passes | Median / p90 turn | Tokens per turn |
|---|---|---|---|---|
| Granite 3B, own turn | 4.61 | 31/36 | 38 s / 95 s | 20.5k |
| Granite 3B + plans | 4.81 | 36/36 | 19 s / 63 s | 4.5k |
| **Qwen3.5 4B, own turn** | **4.86** | **36/36** | 28 s / **52 s** | 20.4k |
| Qwen3.5 4B + plans | 4.81 | 34/36 | 22 s / 69 s | 4.5k |

- **Qwen's own turn is the best result of the four.** It's the most accurate and never failed. Its median is 10 s faster than Granite's own turn, and its slowest turns much faster (52 s against 95 s). It got there without plans or a second model. It ran with thinking off, so it never hit the thinking watch's stop-and-retry cycles that slowed Granite (test 7: 54 s against 168 s).
- **Plans help Qwen far less than Granite.** Tokens dropped 4.5x and the median dropped 7 s, but accuracy dipped and the slowest turns got slower. With planning time added (~11 s for Qwen3 8B), Qwen + plans would be slower than Qwen alone.
- **Where Qwen + plans lost points:**
  - **Test 7 (3.3/5):** `pick` chose the wrong note twice (a one-on-one note, not the project note with the power budget). Qwen's `draft`s, forced into JSON, came out as `}`, `{0.13}`, `{ 30.4 / 5.35 }`, which `calculate` rejects. Granite didn't wrap values like this.
  - **Test 24:** the reply was right ("MOQ) is **100**"); the scorer's `moq\D{0,6}100` pattern is too narrow.

**What it means:** switching the base model to Qwen3.5 4B gives most of what the plan-then-act restructure was for, more accurate and faster, without restructuring. The costs: 3.4 GB against 2.2 GB, sampling not yet tuned, and only 12 plan-friendly tests at 3 repeats. The full 34-test suite on Qwen is the next check.
