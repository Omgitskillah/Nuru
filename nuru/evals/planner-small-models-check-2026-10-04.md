# Small planner models check, 2026-10-04

> **Historical record.** Plan mode was removed on 2026-10-24 (PR #37). The planner, executor, planner Modelfiles and `planner_check/` files this page links to no longer exist on main; they are at commit [7c2c345](https://github.com/Omgitskillah/Nuru/tree/7c2c3458657409a6be66fa88036c09f01cd7a500).

Can Granite 4 (350M, 1B) or Qwen3.5 (0.8B, 2B), set up only as planners, reliably break a request into a JSON plan of steps?

**Short answer: no.** None of the four is reliable.
- **Best whole-plan pass rate on the 20 test prompts:** Granite 1B, 6/20.
- **Multi-step decomposition barely happens.** On the 9 prompts whose reference plan chains three or more steps, the four models passed 4 times out of 36 tries. Every one of those passes was a near-copy of an example in the system prompt.
- **The commonest failure:** the plan stops after its first step, with no `answer` step.
- **Unsafe plans:** some models guess destinations, overwrite a note with placeholder text, or write to notes nobody named.

## Setup

- **Models:** each built from its attached Modelfile, unchanged, in [modelfiles/planner/](../modelfiles/planner/).
  - The four files differ only in their `FROM` line. The Qwen files add their sampling lines.
  - All four: temperature 0, seed 42, `num_ctx` 4096, `num_predict` 1536.

| Model | Base | Parameters | Quantization | Loaded size | Load time | Output speed |
|---|---|---|---|---|---|---|
| `granite-planner:350m` | granite4:350m | 352M | BF16 | 0.84 GB | 1.8 s | 118 tok/s |
| `granite-planner:1b` | granite4:1b | 1.6B | BF16 | 3.64 GB | 5.8 s | 31 tok/s |
| `qwen35-planner:0.8b` | qwen3.5:0.8b | 873M | Q8_0 | 1.10 GB | 2.6 s | 93 tok/s |
| `qwen35-planner:2b` | qwen3.5:2b | 2.3B | Q8_0 | 2.40 GB | 5.6 s | 44 tok/s |

- **Runtime:** Ollama 0.35.1, on Metal; every model ran entirely on the GPU.
- **Loaded size:** taken from `ollama ps`. That is the model plus its 4096-token context, which is the closest available measure of peak memory.
- **Requests:** each request is exactly like the attached example requests.
  - One user message, a fresh conversation for every prompt.
  - `format` set to [plan.schema.json](planner_check/plan.schema.json), `stream: false`, `keep_alive: 5m`.
  - `think: false` for Qwen.
- **Runs:** two runs per prompt per model, one model loaded at a time. Each model got a warm-up call first, so the latencies below are warm. Nothing in any plan was executed.
- **Prompts:** 33 in all.
  - **The 20 from the evaluation doc,** with its reference plans: [cases.json](planner_check/cases.json).
  - **13 from yesterday's runs:** the 12 plan-vs-free suite prompts plus the plan-mode check's March-budget prompt, in [cases_yesterday.json](planner_check/cases_yesterday.json). Their reference plans are mine, converted from yesterday's hand-written plans ([decision_layer/plans/](decision_layer/plans/)) into the new step language.

### Changes from the spec, for audit

- **No change to the spec, Modelfiles, schema or tool contracts.** Two concerns were checked before the run:
  - **Key order:** yesterday Ollama forced schema keys into alphabetical order, so `args` would have had to come before `id`. Rechecked after the Ollama update: a plain two-key schema and the nested `anyOf` plan schema both keep their order (`id` first). No key renaming was needed.
  - **Context size:** the system prompt plus request is 1,884–1,959 tokens. With `num_predict` 1536 that peaks at about 3.5k, inside `num_ctx` 4096. No call hit its output limit (`done_reason` was always `stop`), so `num_ctx` was left at 4096.
- **Is the early stopping caused by the grammar?** I reran three prompts on both Qwen models without `format`. The output was identical, token for token, so the early stopping is the models' own behaviour.
- **Two accepted alternatives were added to the reference plans:**
  - case 08 also accepts `note_info` (the doc's "valid but broader" rule)
  - Y10 also accepts an empty plan.
- **Scoring** ([check.py](planner_check/check.py)):
  - Structure and dependencies are mechanical.
  - Tool choice, arguments and fidelity are mechanical for plans that line up step by step with the reference. Free-text fields (search query, expert question, write content, `pick` wants) may be paraphrased, and I checked each of those by hand.
  - **Where a plan's shape differs from the reference,** fidelity is 0. Arguments still score 1 if every tool step's arguments match a reference step's.
  - **An empty plan where a plan was expected** scores 1 (structure) out of 5.
  - **Cases 19, 20, Y07, Y14, Y15 and Y26** expect an empty plan: 5 points for `{"steps":[]}`, otherwise 0, as the doc specifies.

## Results

Scores are averaged over the two runs. Only one plan differed between runs: Granite 1B on case 11. Otherwise temperature 0 and the seed made every model deterministic.

**The 20 doc prompts:**

| Model | Score /100 | Whole-plan pass | Structure | Tool choice | Arguments | Dependencies | Fidelity | Median latency | p90 | Max |
|---|---|---|---|---|---|---|---|---|---|---|
| Granite 350M | 36 | 2/20 | 18 | 6 | 7 | 3 | 2 | 0.5 s | 1.2 s | 2.8 s |
| **Granite 1B** | **50.5** | **6/20** | 18 | 10 | 10 | 6.5 | 6 | 1.3 s | 2.2 s | 5.4 s |
| Qwen3.5 0.8B | 34 | 0/20 | 18 | 8 | 7 | 1 | 0 | 0.5 s | 0.7 s | 0.9 s |
| Qwen3.5 2B | 47 | 1/20 | 18 | 13 | 13 | 2 | 1 | 1.3 s | 2.6 s | 7.7 s |

**Yesterday's 13 prompts:**

| Model | Score /65 | Whole-plan pass | Passes that are empty plans | Median latency |
|---|---|---|---|---|
| Granite 350M | 16 | 1/13 | 0 | 0.5 s |
| Granite 1B | 38 | 6/13 | 5 | 0.3 s |
| Qwen3.5 0.8B | 22 | 2/13 | 2 | 0.4 s |
| Qwen3.5 2B | 32 | 4/13 | 3 | 1.3 s |

**A model that always returned an empty plan** would score 10/100 (2 passes) on the doc set and 25/65 (5 passes) on yesterday's set. Granite 1B's yesterday result is mostly that:
- It returned an empty plan for 11 of the 13 prompts, including 6 that needed a plan.
- Only one of its passes there (Y28) is a non-empty plan.

### Per case

Score out of 5 (run 1/run 2 where they differed), with the median latency.

| Case | Near an example? | Granite 350M | Granite 1B | Qwen3.5 0.8B | Qwen3.5 2B |
|---|---|---|---|---|---|
| 01 Read a named section |  | 1 · 0.7 s | 1 · 1.9 s | 3 · 0.5 s | 3 · 1.1 s |
| 02 Find a note by meaning | yes | 2 · 0.5 s | 2 · 1.9 s | 2 · 0.5 s | 2 · 1.0 s |
| 03 Search for literal code | yes | 1 · 0.7 s | 3 · 1.1 s | 1 · 0.2 s | 3 · 1.1 s |
| 04 Search using a regular expression |  | 1 · 0.5 s | 3 · 1.2 s | 3 · 0.7 s | 3 · 1.1 s |
| 05 Browse recent notes | yes | 1 · 1.0 s | 1 · 0.2 s | 2 · 0.7 s | 2 · 0.9 s |
| 06 List tag counts |  | 1 · 1.2 s | 5 · 1.1 s | 1 · 0.4 s | 3 · 0.8 s |
| 07 Find notes with a tag |  | 3 · 0.2 s | 5 · 1.2 s | 3 · 0.4 s | 3 · 0.9 s |
| 08 Get tags for one note |  | 3 · 0.5 s | 5 · 1.3 s | 3 · 0.4 s | 3 · 1.0 s |
| 09 Inspect note metadata | yes | 2 · 0.3 s | 5 · 1.3 s | 3 · 0.5 s | 3 · 0.9 s |
| 10 Calculate an expression |  | 1 · 0.8 s | 1 · 0.3 s | 1 · 0.5 s | 1 · 2.4 s |
| 11 Compute statistics from supplied numbers |  | 3 · 0.3 s | 2/1 · 3.5 s | 1 · 0.6 s | 1 · 7.3 s |
| 12 Extract numbers before computing | yes | 1 · 0.4 s | 5 · 3.0 s | 1 · 0.4 s | 1 · 1.7 s |
| 13 Compare two known notes | yes | 5 · 0.6 s | 5 · 2.2 s | 2 · 0.4 s | 5 · 1.9 s |
| 14 Append exact content |  | 1 · 2.5 s | 2 · 1.4 s | 1 · 0.4 s | 3 · 1.3 s |
| 15 Write to the daily note | yes | 5 · 0.4 s | 3 · 1.2 s | 3 · 0.6 s | 3 · 1.3 s |
| 16 Replace an entire note |  | 1 · 2.7 s | 1 · 2.0 s | 1 · 0.4 s | 2 · 1.4 s |
| 17 Move a note to trash | yes | 1 · 0.2 s | 1 · 0.2 s | 2 · 0.6 s | 3 · 1.4 s |
| 18 Ask an expert with attachments |  | 3 · 0.4 s | 1 · 0.2 s | 1 · 0.4 s | 3 · 1.6 s |
| 19 Detect a missing destination | yes | 0 · 0.2 s | 0 · 1.7 s | 0 · 0.6 s | 0 · 1.0 s |
| 20 Recognize an unrepresentable workflow |  | 0 · 1.2 s | 0 · 1.4 s | 0 · 0.5 s | 0 · 3.0 s |
| Y03 Two lookups (suite 3) | yes | 2 · 0.7 s | 1 · 0.2 s | 1 · 0.2 s | 1 · 0.7 s |
| Y04 What happened on a date (suite 4) | yes | 2 · 0.6 s | 1 · 0.2 s | 1 · 0.2 s | 2 · 1.5 s |
| Y07 Weighted average, battery life (suite 7) |  | 0 · 0.7 s | 5 · 0.2 s | 0 · 0.4 s | 0 · 3.5 s |
| Y08 Statistics from a found note (suite 8) | yes | 1 · 0.2 s | 1 · 0.2 s | 2 · 0.4 s | 1 · 1.9 s |
| Y10 Look up and compare (suite 10) | yes | 1 · 1.9 s | 5 · 0.2 s | 2 · 0.5 s | 5 · 0.8 s |
| Y14 Derive a count from note values (suite 14) |  | 0 · 0.5 s | 5 · 0.2 s | 0 · 0.4 s | 5 · 0.6 s |
| Y15 Weighted sums over a table (suite 15) |  | 0 · 0.2 s | 5 · 0.2 s | 5 · 0.2 s | 0 · 1.7 s |
| Y24 Summarize a found note (suite 24) | yes | 1 · 1.6 s | 1 · 0.2 s | 1 · 0.2 s | 2 · 1.3 s |
| Y26 Delete by content (suite 26) |  | 0 · 0.4 s | 5 · 0.2 s | 5 · 0.2 s | 5 · 0.7 s |
| Y28 Daily note with given content (suite 28) | yes | 1 · 0.5 s | 5 · 1.9 s | 1 · 0.4 s | 3 · 1.4 s |
| Y29 Append to a found note (suite 29) |  | 2 · 0.4 s | 2 · 1.8 s | 2 · 0.5 s | 1 · 1.1 s |
| Y34 Unit arithmetic (suite 34) |  | 5 · 0.4 s | 1 · 0.2 s | 1 · 0.5 s | 5 · 1.5 s |
| YPM Find, sum, write total (plan-mode check) | yes | 1 · 2.8 s | 1 · 0.2 s | 1 · 0.4 s | 2 · 3.7 s |

**Near an example** means that the prompt has the same shape as one of the system prompt's examples. 16 of the 33 cases do; the reason for each is in the cases files. Granite 1B passed 5 of those 16 and 7 of the other 17; the other models passed too few on either side to compare.

Every plan, with its checker notes, is listed in [planner_check/results/plans-2026-10-04.md](planner_check/results/plans-2026-10-04.md).

## What goes wrong

1. **Plans stop after the first step.** The model writes one correct-looking tool step and closes the array, with no `answer` and no follow-on steps. Counting only the cases that needed a plan:
   - **Granite 350M:** 15 of 18 doc cases
   - **Qwen 0.8B:** 16 of 18 doc cases; its only two-step plan was case 04's find → answer
   - **Qwen 2B:** 15 of 18 doc cases
   - **Granite 1B:** 7.5 of 18 (averaged over the two runs)

   The tool choice and arguments in that first step are often right. For example, Qwen 2B's first steps are right on cases 01, 03, 04, 07, 09, 14, 15, 17 and 18.
2. **Multi-step chains don't survive.** Nine prompts need three or more steps: 02, 12, 13, Y03, Y04, Y08, Y24, Y29 and YPM. Across the four models (36 tries), the only passes were:
   - 13 (compare two notes), on Granite 350M, Granite 1B and Qwen 2B
   - 12 (read → pick → statistics), on Granite 1B

   Both are near-copies of examples in the prompt. The search → pick → read chain is demonstrated in the Berlin example, yet no model got it right on any prompt that needed it:
   - case 02 stops after search, or after search and pick
   - Y24 stops after search
   - Y03 is empty or a jumble
3. **No model returned an empty plan when it should have** on cases 19 and 20; every model scored 0 on both.
   - **19 (archive folder I have in mind):** Granite 1B and Qwen 0.8B planned a move to a guessed `Archive/…` path; the other two browsed folders.
   - **20 (summarise, then write the summary):**
     - Qwen 2B planned `write_note` in **replace** mode to `Summaries/Heat pumps.md` with the placeholder content "Summary of Research/Heat pumps.md".
     - Granite 350M wrote the note's *path* to the daily note as content.

   On yesterday's set, Granite 1B returns empty plans freely, which also makes it refuse doable tasks (Y03, Y04, Y08, Y24, Y34, YPM).
4. **Unsafe or wrong writes** (run 1):
   - **Granite 350M**
     - 14 and 16: alternates `read_note` and `write_note` up to the 12-step cap, with no target note.
     - Y24: writes a placeholder "summary" to the daily note, which nobody asked for.
     - YPM: nine identical writes of `$s2.expenses`, a partial reference the language doesn't allow.
   - **Granite 1B**
     - 14: writes to the *daily* note and puts `Home/Maintenance.md` in `targets`.
     - 19: plans a move to the guessed `Archive/Website.md`.
   - **Qwen 0.8B**
     - 19: plans a move to the guessed `Archive/Work/Website.md`.
     - 17: writes `Trash/` for the user's `trash/`.
   - **Qwen 2B**
     - 20: the placeholder replace described in item 3.
     - YPM: content `"Total grocery amount: $${s3.sum}"`, a template the executor can't resolve.
5. **Arithmetic and statistics get mixed up:**
   - case 11: Granite 1B and Qwen 0.8B compute the mean by hand with `calculate`. In its second run, Granite 1B also wrote the variance by hand with mean 23.4 filled in.
   - case 10: models search for the expression (Qwen 0.8B), return an empty plan (Granite 1B), or split it into wrong pieces. Granite 350M's `145.50 + 29.95 * 1.19` changes the arithmetic. Qwen 2B's first step is right, but it adds a second `answer` step and another `calculate` after it.
   - Y08: Qwen 2B plans `calculate "9"`.
6. **Qwen 2B looped once.** On Y07, both runs repeated tokens until Ollama aborted the call ("token repeat limit reached", HTTP 500, about 3.5 s).

## If the harness added the final answer step

Yesterday's planner added a missing `answer` step in code. As a what-if, I rescored with that fix: when a plan's last step isn't `answer`, the harness appends one, fed by every step nothing else uses.

| Model | Doc 20: score / passes | Yesterday 13: score / passes |
|---|---|---|
| Granite 350M | 51 / 5 | 21 / 1 |
| Granite 1B | 61 / 9 | 39 / 6 |
| Qwen3.5 0.8B | 55 / 5 | 28 / 2 |
| **Qwen3.5 2B** | **72 / 12** | 36 / 5 |

That would make **Qwen 2B a reasonable picker for single-tool requests.** Its first steps are often right: tool, arguments, `literal` and `section`. It still wouldn't plan:
- chains (02, 12, Y03, Y08, Y24, Y29, YPM)
- empty-plan cases (19, 20)
- writes needing exact metadata (05's three filters, 16's `replace`)

## Conclusion

- **For decomposition, these models aren't usable.** None of the four can reliably break a request into its steps. What they can do is choose the first tool, and Qwen 2B is best at that.
- **Their one strength is speed.** All four answer in about 0.5–1.5 s warm, which is 10–20× faster than Qwen3 8B's 11 s median yesterday.
- **This matches yesterday's result:** nothing under 4B held the plan format and the tool details together. Yesterday Qwen3.5 0.8B and 2B passed 0/5 on the March-budget prompt; here they planned it no better (1–2/5).
- **Options, if a small planner is still wanted:**
  - **Fixed plan templates** (lookup / compute / write / find) chosen by `tool_gate`. Yesterday's suggestion; a small model like Qwen 2B would fill only the first step's arguments, which is the part it gets right here.
  - **A fine-tuned 1–2B planner**, trained on plans from Qwen3 8B or Claude.
  - **Qwen3.5 4B** on these 33 prompts. It was the first model under 8B to pass yesterday (2/5), and this suite would show whether that holds.

## Files

- [modelfiles/planner/](../modelfiles/planner/): the four Modelfiles, as attached.
- [planner_check/](planner_check/), containing:
  - `plan.schema.json` and `tool-contracts.json`, as attached (the contracts are identical to the proposed ones)
  - `cases.json` and `cases_yesterday.json`: the prompts, reference plans, near-example flags and scoring notes
  - `run.py` (send the requests) and `check.py` (score them)
  - `results/`: the raw responses, the scores, and every plan
- **To re-run:** `python3 run.py OUT.jsonl`, then `python3 check.py OUT.jsonl scores.json`. Re-scoring after a reference change needs no rerun.

## Added afterwards: Qwen3.5 4B

`qwen35-planner:4b` was built from the same Qwen Modelfile with only `FROM qwen3.5:4b` changed ([qwen3.5-4b-planner.Modelfile](../modelfiles/planner/qwen3.5-4b-planner.Modelfile)).
- **The model:** 4.7B parameters, Q4_K_M quantization, 3.18 GB loaded, 6.6 s to load.
- **The run:** same 33 prompts, two runs each. Both runs were identical.
- **Raw files:** `planner_check/results/*-4b-2026-10-04.*`.

| Set | Score | Whole-plan pass | Structure | Tool choice | Arguments | Dependencies | Fidelity | Median | p90 | Max |
|---|---|---|---|---|---|---|---|---|---|---|
| Doc 20 | **82/100** | **14/20** | 19 | 18 | 16 | 15 | 14 | 3.1 s | 3.9 s | 5.1 s |
| Yesterday 13 | 37/65 | 5/13 | 10 | 6 | 9 | 7 | 5 | 3.5 s | 5.4 s | 7.3 s |

**What it gets right:**
- **It plans steps that build on each other.** Search → pick → read worked on 02, Y04 and Y24, including two prompts that aren't near-copies of the examples. Read → pick → statistics worked on 12, and search → pick → read → pick → statistics on Y08.
- **It returned the empty plan on 19** (no guessed archive), and on Y26.

**Where it fails:**
- **The answer step is still missing in 4 of the doc cases** (11, 16, 18) and in Y28/Y29. On 11 and 18 the plan is otherwise exactly right.
- **The write metadata is wrong:**
  - 14 reads the note, then writes with `supersedes: "$s2"` and no target `note`.
  - 16 puts the target path in `supersedes`, not `note`.
  - Y29 writes to the daily note with `targets`/`supersedes`, not Priya's note.
- **05 (browse) drops the `type` and `limit` filters** and tries to pick the newest three.
- **20 is unsafe.** It plans read → pick the full content → `write_note` **replace** into `Summaries/Heat pumps.md`, which copies the note instead of summarising it, overwriting the target.
- **Yesterday's set doesn't go well:**
  - it plans where an expression would be needed (Y07, Y14, Y15; Y15 builds `"$s4 - $s5"`, which is not a valid whole-value reference)
  - it answers from search passages without reading (Y03, Y10)
  - it stops after search → pick on YPM

**Compared with the smaller models,** it's the first model in this check that decomposes multi-step requests. On the doc set its 14/20 against 6/20 for Granite 1B is the biggest gap. It's 2–3× slower (about 3 s against 1 s) but still far faster than Qwen3 8B's 11 s from yesterday. A harness-added `answer` step would turn three more doc cases into passes (11, 18 and, apart from its metadata, 16). Write-metadata safety still needs code checks.

## Added afterwards: thinking on

Each model was rerun at its lowest thinking setting that is actually on. Nothing else changed: same Modelfiles, 33 prompts, two runs each, and `run.py --think`. Raw files: `planner_check/results/*-think-2026-10-04.*`.
- **Granite 4 350M and 1B can't think.** Ollama rejects `think` for both ("does not support thinking"), and `ollama show` lists no thinking capability. Their results above are their only setting.
- **Qwen3.5 has only off and on** (`ollama show`: levels false, true). `think: "low"` is accepted but gave output identical to `true`, so on is the lowest thinking setting. All three Qwen models were rerun with `think: true`.
- **Thinking tokens count toward the Modelfile's `num_predict` 1536.** 8 calls hit that cap before writing a usable plan: Qwen 0.8B on 18, Y26 and YPM, and Qwen 2B on 11, both runs each time. They're scored as failures.

| Model | Doc 20, thinking off | Doc 20, thinking on | Yesterday 13, thinking off | Yesterday 13, thinking on | Median time, off → on |
|---|---|---|---|---|---|
| Qwen3.5 0.8B | 34, 0/20 | 32, 1/20 | 22, 2/13 | 18, 1/13 | 0.5 → 2.3 s |
| Qwen3.5 2B | 47, 1/20 | 55, 4/20 | 32, 4/13 | 27, 2/13 | 1.3 → 5.5 s |
| **Qwen3.5 4B** | 82, 14/20 | **92, 16/20** | 37, 5/13 | 41, 6/13 | 3.1 → 9.8 s (p90 14 s, max 22 s) |

**Qwen 4B with thinking on:**
- **It gets every structure, tool-choice and empty-plan point on the doc set.**
  - It now returns the empty plan on both 19 and 20. It no longer overwrites `Summaries/Heat pumps.md`.
  - Only one plan lacks the final answer step (18).
- **Its 4 doc failures are all argument errors:**
  - **05** puts all three filters inside the `folder` string: `"Work\",\"limit\":3,\"type\":\"meeting"`.
  - **14** has no target `note`, and fills the unused fields with the string `"null"`.
  - **16** puts the path in `supersedes`, not `note`, and adds `type: "daily"`, `author: "user"` and `tags: ["reminder"]`, none of which the request gave. With replace mode, that plan would **overwrite today's daily note**. A code check on write arguments is still needed.
- **On yesterday's set it still plans requests that need a built expression** (Y07, Y14, Y15), and writes a non-whole reference on YPM (`"Total grocery: $s5.sum"`).

**For the two small Qwen models, thinking doesn't help reliably.**
- **Qwen 2B:** the doc set improves (19 now empty, 10 now right), but 11 hits the token cap and yesterday's set gets worse.
- **Qwen 0.8B:** no better, and it hits the cap on 3 prompts.

**Overall, Qwen3.5 4B with thinking on is the best planner tested here** (16/20), but it takes about 10 s per plan, close to Qwen3 8B's 11 s from yesterday. With thinking off it's 14/20 at about 3 s. In both modes, write arguments need checking in code before a plan runs.

## Added afterwards: Qwen3.5 9B, thinking off, one run

`qwen35-planner:9b` was built from the same Qwen Modelfile with only `FROM qwen3.5:9b` changed. The model has 9.7B parameters at Q4_K_M quantization.
- **The run:** 33 prompts, one run each, thinking off.
- **Sampling:** [sample_resources.sh](planner_check/sample_resources.sh) recorded resources every ~0.5 s. Raw files: `planner_check/results/*-9b-2026-10-04.*`.

| Set | Score | Whole-plan pass | Median latency | Max |
|---|---|---|---|---|
| Doc 20 | 82/100 | 14/20 | 6.8 s | 8.6 s |
| Yesterday 13 | 38/65 | 5/13 | 10.2 s | 18.6 s |

**It scores the same as Qwen 4B with thinking off on the doc set** (82, 14/20) **at about twice the time.** Its failures are of the same kinds:
- **Write metadata:**
  - 14 writes to the daily note with the target path in `supersedes`.
  - Y29 does the same.
- **Case 20 is unsafe:** it plans `write_note` in **replace** mode to `Summaries/Heat pumps.md` with the placeholder content `"[Summary of Research/Heat pumps.md]"`.
- **19 doesn't return an empty plan;** it plans a harmless `note_info`, where 4B returned the empty plan.
- **03** escapes the brackets (`config.timeout\[0\]`) even though `literal` is true.
- **05** packs the filters into the `folder` string.
- **06** has no answer step.
- **Yesterday's set:**
  - Y07, Y14 and Y15 are planned instead of returned empty; Y07 invents `$s4.power`.
  - YPM uses `"…$s5.mean"`, and with the mean, not the sum.
  - Y04 and Y08 use `find_in_notes` where a search is needed.

**Resources on the Apple M4 (16 GB unified memory, 10 cores), over the 310 s run:**
- **CPU:** Ollama's process never went above 12%.
- **GPU:** above 90% busy in 94% of samples. The only idle moments were the gaps between requests.
- **GPU memory:** 6.5 GB median, 7.2 GB peak. `ollama ps` reports 5.5 GB; the rest is working space.
- **System free memory:**
  - It fell from 74% before the run to 17% at worst, during loading.
  - It held at about 26% while running, and recovered to 68% after the model unloaded.
- **Swap:** 5.9 GB before the run, 6.2 GB at peak, so the pressure was moderate this time. Before the single-prompt test it was 8.4 GB, and loading the model then pushed it to 9.2 GB, near the limit.
- **Speed:**
  - First call: 24 s cold, including the 9.9 s load.
  - Once warm: reading the 1.9k-token prompt took about 4 s, and writing ran at 18 tok/s, against 4B's 3 s total per plan.

**On this Mac, 9B is not worth it as a planner.** It is no more accurate than 4B, and takes twice the time and about twice the memory.

## Added afterwards: Gemma 4 e2b and e4b

- **Download:** `gemma4:e2b` was downloaded for this test; `gemma4:e4b` was already installed.
- **Modelfiles:** there's no attached Gemma Modelfile, so [gemma4-e2b-planner.Modelfile](../modelfiles/planner/gemma4-e2b-planner.Modelfile) and [gemma4-e4b-planner.Modelfile](../modelfiles/planner/gemma4-e4b-planner.Modelfile) use the same system prompt with Granite's settings (temperature 0, seed 42, `num_ctx` 4096, `num_predict` 1536). Gemma's own sampling defaults (temperature 1, top_k 64) are overridden.
- **Thinking:** Gemma 4 has only off and on, like Qwen3.5. Both models ran both ways.
- **Runs:** 33 prompts × 2 runs per setting, one model loaded at a time. Both runs were identical everywhere, and no call hit the output cap.
- **Raw files:** `planner_check/results/*-gemma-*-2026-10-04.*`.

| Model | Thinking | Doc 20 score | Doc 20 passes | Yesterday 13 | Median latency (doc) | p90 | Max |
|---|---|---|---|---|---|---|---|
| Gemma e2b (4.6B total, about 2B effective) | off | 58 | 5/20 | 17, 0/13 | 1.2 s | 1.5 s | 1.7 s |
| Gemma e2b | on | 59 | 4/20 | 29, 3/13 | 7.3 s | 13.1 s | 27.1 s |
| Gemma e4b (8B total, about 4B effective) | off | 69 | 9/20 | 26, 1/13 | 1.7 s | 3.3 s | 3.6 s |
| **Gemma e4b** | **on** | **81** | **12/20** | 30, 2/13 | 9.8 s | 19.0 s | 50.0 s |

**Gemma e4b with thinking on:**
- **It's the only model besides Qwen 4B (thinking on) to get both 19 and 20 right.** It also gets both exact writes right (14 append, 16 replace), with `note` used properly.
- **6 of its 8 doc failures are only the missing answer step** (01, 05, 06, 10, 17, 18); those plans are otherwise correct. 13 stops after the first read, and 15 starts at `s2`.
- **It does badly on yesterday's set,** with the usual problems: it plans the expression cases and stops chains early.

**Gemma e2b, either way:**
- **It stops after one step in 14 of 20 doc cases.**
- **On 19** it plans a move to a guessed `Archive/Website.md`.
- **On 20** it plans to overwrite `Summaries/Heat pumps.md` with the whole source note, in replace mode with thinking off.

Gemma e4b with thinking off makes the same 19/20 mistakes, plus a placeholder replace on 20.

**What the doc-set scores would be if the harness added a missing final answer step,** for every model tested:

| Model | Thinking off | Thinking on |
|---|---|---|
| Granite 350M / 1B | 51, 5/20 / 61, 9/20 | n/a (can't think) |
| Qwen3.5 0.8B / 2B | 55, 5/20 / 72, 12/20 | 50, 4/20 / 78, 13/20 |
| **Qwen3.5 4B** | 88, 16/20 | **94, 17/20** |
| Qwen3.5 9B | 84, 15/20 | not run |
| Gemma e2b | 79, 13/20 | 82, 14/20 |
| **Gemma e4b** | 82, 14/20 | **92, 17/20** |

**Resources** were sampled every ~0.5 s and split per model; only one model was loaded at a time. Swap stayed at 5.8 GB throughout, and CPU stayed under 4%.

| Model, thinking | Run time | GPU memory, median / peak | GPU >90% busy | Free memory, low / typical |
|---|---|---|---|---|
| e2b off | 85 s | 4.8 / 5.1 GB | 98% of samples | 42% / 44% |
| e4b off | 170 s | 4.2 / 4.6 GB | 95% | 36% / 39% |
| e2b on | 690 s | 4.8 / 5.2 GB | 100% | 38% / 40% |
| e4b on | 902 s | 4.2 / 4.7 GB | 99% | 36% / 39% |

**`ollama ps` misreports Gemma's memory** (0.22 GB for e2b, 3.0 GB for e4b), so the GPU figures above are the ones to use. **e2b measured more GPU memory than e4b.** That's unexpected; e2b's newer packaging (Ollama ≥ 0.30.9, with a draft head and vision/audio parts) may explain it, but I haven't verified that.

**Compared with the others:**
- **Gemma e4b with thinking on is about as good as Qwen 4B with thinking on** on the doc set: 92 against 94 with an added answer step, 81 against 92 without. It's as slow (about 10 s median) and has a worse slow end (max 50 s). It is also worse on yesterday's set (30 against 41).
- **Gemma e4b with thinking off (69, 9/20) is clearly behind Qwen 4B with thinking off (82, 14/20)** at a similar speed.
- **Qwen3.5 4B remains the best option.**
