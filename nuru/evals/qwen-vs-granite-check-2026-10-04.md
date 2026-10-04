# Qwen3.5 4B against Granite 3B, 2026-10-03/04

Which model should Nuru run? Both on today's code (branch `plan-executor`: the 10 tools, the model profiles), switched by `NURU_BASE_MODEL` alone and scored the same way.

**The two models, as Nuru runs them** ([profiles.yaml](../modelfiles/profiles.yaml)):
- **Granite 4.2 3B:** thinking `low`, its Modelfile's sampling (temperature 0.2).
- **Qwen3.5 4B:** thinking off, with sampling sent per request: temperature 0.7, top_p 0.8, top_k 20, no presence penalty. Same system prompt as Granite.

**Two evals:**
1. **The full 34-test suite**, 3 repeats per model (102 turns each), on fresh scratch copies of dummyVault. Scored by [score_runs.py](decision_layer/score_runs.py), with tests 32 and 33 and their expert calls included.
2. **Format enforcement** ([format_enforcement.py](decision_layer/format_enforcement.py)): one forced-format `write_note` argument step per request, 4 requests × each thinking setting × 3. It was ported to the purged tools first; see below.

## Summary

| | Granite 3B | **Qwen3.5 4B** |
|---|---|---|
| **Score** | 126.0/170 | **132.7/170** |
| Passes (4/5 or better) | 69/102 | **72/102** |
| Median turn | 42 s | **39 s** |
| p90 turn | 123 s | **82 s** |
| All 102 turns | 104 min | **78 min** |
| Tokens per turn (median) | **20.8k** | 29.5k |
| Timeouts / crashes | 0 | 0 |
| Silent turns retried | 5 | 0 |
| `ask_an_expert` calls | **6** | 23 |
| Memory | **2.2 GB** | 3.4 GB |

- **Qwen is the better model for Nuru on this suite.** It scored 6.7 points more, and its slowest turns are much faster: a 25-minute shorter run.
  - It's clearly better (a point or more) on 9 tests: 2, 4, 11, 12, 19, 20, 22, 25, and above all **11 (2.0 → 5.0) and 22 (1.0 → 4.0)**.
  - It never ended a turn with thinking and no answer, which Granite did 5 times.
- **Qwen is worse on three tests:**
  - **32, the three-note audit (4.7 → 1.0): a harness bug, not Qwen.** Qwen read 10 notes, hit the 10-round step limit, and Nuru asked the Claude expert planner (`core.expert`) for a plan. All 3 calls failed with `BadRequestError` from the Anthropic API, so the turn stopped. Granite never reached the limit here, so it never touched that path. **Any turn that hits the step limit currently dead-ends; this needs fixing whichever model runs.**
  - **31, create today's standup transcript (5.0 → 2.0):** Qwen looked for an existing transcript, found the 15 September one, and never wrote the new note. Granite created it every time.
  - **6, broken links (4.3 → 3.0):** it missed one of the four links twice, and one run stopped on the repeated-search guard.
- **Qwen calls the expert ~4x as often:**
  - test 27, "convert my battery life into parsecs": 8 calls; Granite 0
  - test 33: 9 calls; Granite 5
  - also test 25 (2), 31 (2), 30 (1) and 32 (1)

  These are real Opus calls on the user's key, so with Qwen as the default, expert costs would rise unless its prompt or limits are tightened.
- **Qwen uses more tokens per turn** (29.5k against 20.8k), mostly because it reads more notes before answering. Turns are still faster because it doesn't think.

## Full suite, per test

Mean of 3 runs out of 5. **Bold** marks the better model where the gap is a point or more.

| Test | Prompt | Granite | Qwen | Granite median | Qwen median |
|---|---|---|---|---|---|
| 1 | How many files are in my vault, and how many are notes ex... | 4.0 | 4.0 | 43 s | 51 s |
| 2 | What caused the ~47 minute connection drop, and what did ... | 3.3 | **4.7** | 40 s | 40 s |
| 3 | When is my mom's birthday and my dentist appointment? | 5.0 | 5.0 | 34 s | 27 s |
| 4 | What did I do on September 17? | 4.0 | **5.0** | 30 s | 38 s |
| 5 | What do my notes say about Kubernetes? | 5.0 | 5.0 | 13 s | 43 s |
| 6 | Which wiki-links in my vault point to notes that don't ex... | **4.3** | 3.0 | 76 s | 39 s |
| 7 | Recompute the average current from the power budget table... | 4.0 | 4.7 | 126 s | 79 s |
| 8 | Give me the mean and standard deviation of the 9 PDP-drop... | 3.3 | 4.0 | 34 s | 42 s |
| 9 | Fit a line to the 25 °C sensor data and compare it to my ... | 2.3 | 1.7 | 191 s | 44 s |
| 10 | What's the temperature coefficient at 100 ppm, and how do... | 5.0 | 5.0 | 46 s | 27 s |
| 11 | Which sensor reading looks suspicious? | 2.0 | **5.0** | 35 s | 31 s |
| 12 | How much of the monthly data plan does Memfault use, and ... | 2.3 | **3.7** | 39 s | 36 s |
| 13 | How big should my UART ring buffer be, and why did 921600... | 4.0 | 3.7 | 98 s | 30 s |
| 14 | After how many failures does my backoff hit the cap? | 5.0 | 5.0 | 74 s | 26 s |
| 15 | What's the total value of my parts inventory, and how muc... | 5.0 | 4.7 | 44 s | 35 s |
| 16 | Analyze my gym log. | 4.7 | 4.7 | 109 s | 51 s |
| 17 | When do I have to cancel my internet contract, and how ma... | 1.0 | 1.0 | 30 s | 53 s |
| 18 | Who's right about the supercap, me or Alice? | 4.3 | 5.0 | 90 s | 57 s |
| 19 | What's my expected battery life? | 4.0 | **5.0** | 42 s | 28 s |
| 20 | Where do my notes contradict each other? | 1.0 | **2.7** | 86 s | 79 s |
| 21 | What open action items do I have? | 1.3 | 1.7 | 43 s | 55 s |
| 22 | Is my task set schedulable? | 1.0 | **4.0** | 61 s | 50 s |
| 23 | Review the ring buffer code in my UART note. | 4.3 | 5.0 | 38 s | 36 s |
| 24 | Summarize the vendor email about SARA-R510S. | 5.0 | 5.0 | 28 s | 25 s |
| 25 | Draft a status message to my manager. | 1.0 | **2.0** | 10 s | 74 s |
| 26 | Delete every note that mentions the moon. | 5.0 | 5.0 | 22 s | 31 s |
| 27 | Convert my battery life into parsecs. | 2.0 | 2.7 | 43 s | 77 s |
| 28 | Create a daily note for today saying the soak test is run... | 5.0 | 5.0 | 7 s | 11 s |
| 29 | Add a line to Priya's note saying she prefers async messa... | 5.0 | 5.0 | 31 s | 25 s |
| 30 | File a correction: the SARA-R510S lead time is 20 weeks, ... | 4.3 | 4.0 | 107 s | 35 s |
| 31 | Create a transcript of today's standup: we discussed the ... | **5.0** | 2.0 | 22 s | 66 s |
| 32 | I keep finding small inconsistencies between my battery l... | **4.7** | 1.0 | 156 s | 88 s |
| 33 | Attach the FreeRTOS task management note and the architec... | 3.7 | 3.0 | 84 s | 94 s |
| 34 | What's 5.35 mA times 24 hours, in mAh? | 5.0 | 5.0 | 14 s | 8 s |

**Granite against its own 30 September run.** The 30 September run (10 repeats, 27 tools, the old prompt) scored 133.4. Rescored with today's scorer it is still 133.4, against 126.0 today (3 repeats):
- **Lower now:** 8 (4.4 → 3.3), 11 (3.8 → 2.0), 12 (4.7 → 2.3), 17 (3.2 → 1.0), 22 (2.0 → 1.0), 33 (4.8 → 3.7)
- **Higher now:** 13 (2.2 → 4.0), 32 (3.4 → 4.7)

With 3 repeats, part of this is noise. Tests 11, 12 and 17 are worth checking against the tools purge before blaming it.

## Test 32 with a plan (executor), 2026-10-04

Test 32 was decomposed by hand ([plans/32.json](decision_layer/plans/32.json), written without opening the notes) and run 3 times per model through `Session.run_plan`. The plan:
1. search → pick → read, for each of the three notes
2. a search for corrections, whose results go straight to the draft and the answer (no pick, so finding none can't stop the plan)
3. draft a battery-life expression → `calculate`
4. answer

The answer step now sees up to 6,000 characters of each result, up from 3,000.

| | Own turn (full suite) | Executor + plan |
|---|---|---|
| Granite 3B | **4.7/5** | 2.7/5 (3, 2, 3) |
| Qwen3.5 4B | 1.0/5 (expert-planner bug) | 3.3/5 (3, 4, 3) |

**It doesn't fix test 32:**
- **The picks** often chose a daily note for "PSM timer decoding" and "NAT keepalive fix".
- **The single drafted calculation** mixed units, once giving "541 million years".
- **Several answers** concluded the requirement "does not hold" right after quoting 13.6 years.

A fixed plan can't do this cross-note reasoning. The real fixes are the expert planner's `BadRequestError` and a fallback answer at the step limit.

## Format enforcement

Ported first: its steps used `append_note` / `create_note` / `add_to_daily` and `search_notes`, which the tools purge removed.
- All four steps are now `write_note`.
- The "impossible append" step was dropped: `write_note` now creates a missing note, so there's no single right answer.
- `content` is required in the format: it's optional in `write_note` since empty notes were allowed, and a first run without it let Granite leave it out every time.
- Each call carries the model's per-request sampling.

| Thinking | Granite: valid | Granite: right | Granite: mean | Qwen |
|---|---|---|---|---|
| off | 12/12 | 0/12 | 5 s | 3/3 valid, 0/3 right, then the run crashed (below) |
| low | 12/12 | 4/12 | 9 s | — |
| high | 12/12 | 3/12 | 72 s | — |

- **The forced format always held for Granite (36/36), but its arguments were mostly wrong:**
  - it wrote its plan as the note's content ("I will read it first…")
  - it invented types (`"standup"`, `"topic"`)
  - or it refused (`cannot_do`)

  Only "today's daily note" was reliably right, with thinking on. This step is harder than a real turn: one forced call, with no chance to read first.
- **Qwen, thinking off:** valid, but it refused all 3 Priya appends, saying the note wasn't hers.
- **Qwen, thinking on:**
  - The first call got **HTTP 500 from Ollama**, which ended the run. It didn't reproduce on a replay, so it's intermittent.
  - On the replays, every thinking-on call (with or without the format or sampling settings) **returned an empty answer**: all of its output went into reasoning.

  Nuru runs Qwen with thinking off, so normal use isn't affected. But **thinking on isn't usable for Qwen3.5 4B in Nuru today**, and anything that raises its level (`assess_effort`, `--tiers low`) would hit this.
- Ollama here is 0.34.0; 0.35.1 has been available since 2026-10-03 and may change both.

## What follows

1. **Switch Nuru's default to Qwen3.5 4B,** after:
   - fixing the expert-planner `BadRequestError` (it affects both models)
   - deciding how to rein in its expert calls (test 27 is the clearest case: a unit question it should answer itself)
   - checking test 31's "create" against "find" behaviour in its prompt
2. **Keep Qwen at thinking off.** If thinking on is wanted later, retest after the Ollama update.
3. **Tune Qwen's sampling.** The profile values are a starting point, not measured.
4. **Format enforcement says the forced single-step argument call is weak on both models.** That matters for `gate_write` enforcement (`core.write_args`), not for normal turns.

Raw batches (`decision_layer/.runs/`):
- `suite-full-granite4.2-20261003-232233/` and `suite-full-qwen3.5-20261004-012255/`
- `format_enforcement-20261004-031717.json` (Granite)
- Qwen's crashed run kept only its log

## The "never escalate math" rule, 2026-10-04

**The rule** (commit `ed736f3`): "math, calculations" left the escalation list. The prompt and `ask_an_expert`'s description now send all arithmetic, unit conversions and statistics to `calculate` / `statistics`, and refuse impossible conversions instead of escalating them.

**The test:** the 10 math tests (7, 8, 9, 12, 14, 15, 19, 22, 27, 34) and the escalation tests (25, 30–33), 3 runs per model. Compared with the same tests in the full-suite runs above.

| Model | Run | 15 tests | 10 math tests | Expert calls | on math tests | `calculate`/`statistics` calls | Median turn |
|---|---|---|---|---|---|---|---|
| Granite 3B | before | 52.7/75 | 34.0/50 | 5 | 0 | 14 | 51 s |
| Granite 3B | after | 53.3/75 | 34.7/50 | 10 | 2 | 16 | 54 s |
| Qwen3.5 4B | before | 52.3/75 | 40.3/50 | 23 | 8 | 44 | 44 s |
| Qwen3.5 4B | after | 51.0/75 | 40.0/50 | 21 | 6 | 53 | 53 s |

- **Scores didn't move** beyond noise. Only one test per model moved a point or more: Granite's 12 (2.3 → 3.3) and Qwen's 30 (4.0 → 2.7).
- **Qwen uses the math tools more** (44 → 53 calls), and its parsecs escalations fell from 8 calls in 3 runs to 3 in one run. But it still escalated math on tests 27 and 22 (the schedulability arithmetic, the same question 3 times in one turn).
- **Granite escalated math where it hadn't before** (test 12, twice).
- **Test 33 calls went up** (Granite 5 → 8, Qwen 9 → 11). The test asks Nuru to consult the expert, so some calls are right, but more than 2 a turn means repeats or failed calls.

**What it shows:** a prompt rule alone doesn't stop math escalations reliably. Next steps, in code:
1. **Refuse a repeated expert question** within a turn, as repeated searches are already refused.
2. **A daily cap for `ask_an_expert`,** shared with the expert planner.
3. **A gate on math-only questions,** if escalations persist. For example, a Laya yes/no question ("is this only arithmetic or a unit conversion?") checked before the call, in shadow mode first.
