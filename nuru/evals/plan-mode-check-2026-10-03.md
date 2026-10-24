# Plan mode check, 2026-10-03

> **Historical record.** Plan mode was removed on 2026-10-24 (PR #37). The planner, executor, planner Modelfiles and `planner_check/` files this page links to no longer exist on main; they are at commit [7c2c345](https://github.com/Omgitskillah/Nuru/tree/7c2c3458657409a6be66fa88036c09f01cd7a500).

Can Nuru break a request into a plan of steps rather than start carrying it out?

The test prompt:

> PLAN: Find my March budget note, add up the grocery amounts in it, and add the total to today's daily note.

The step schema follows plan-and-execute designs such as ReWOO and LLMCompiler:
- **Tool steps** are run by the harness: `{"step", "tool", "with"}`.
- **Think steps** are run by the model later: `pick` (choose one value from a result) or `draft` (write text a later step needs), as `{"step", "think", "uses", "want"}`.
- **`$sN`** in an argument means the whole output of step sN.

Nothing in a plan is run; this check only looks at the plans.

**Setup:**
- Planner models: `nuru` (granite4.2:3b) and `nuru-8b` (granite4.2:8b), both with the same sampling settings (temperature 0.2, top_p 0.9).
- Five plans per configuration.
- The 10 model tools from the live servers. `ask_an_expert` is left out.

**What counts as a pass:**
1. `check_plan` finds nothing. It checks that references point back, arguments are each tool's own, required arguments are present, and `calculate` gets arithmetic or a `$sN`.
2. The plan also meets a rubric for this prompt:
   - a `search` for the budget note
   - a `pick` from that search
   - a `read_note` of the picked note
   - a `calculate` or `statistics` step fed from the note that was read
   - a `write_note` with `type: "daily"` whose content depends on the computed total

## Summary

- **Inside Nuru's normal turn, Granite never plans.** The earlier runs today (logs from 16:46–16:57) put the PLAN instruction in the Modelfile while tools were still offered. Granite called `search` first in every run, with the full prompt and with the PLAN-only prompt. When tools are offered, it acts. Planning has to be a separate call with no tools offered.
- **The 3B model never produced a passing plan.** Across 4 configurations and 20 plans it passed 0. It gets the outline right (search → read → calculate → write) but makes the same mistakes every time:
  - prose in `calculate` ("sum of grocery amounts") instead of drafting an expression first
  - no `pick` between `search` and `read_note`
  - references into parts of a result (`$s1.matches[0]`, `$s2.path`), even right after a rule saying there's no such thing
- **The 8B model decomposes the task well.** It passed 1 of 5 at thinking off. With two extra prompt hints, it passed 4 of 5 if the daily-note mistake below is counted as correct. Its plans have every step the request needs, in the right order, including a `draft` for the expression.
- **One mistake is left, and it's a tool-design issue:** the 8B writes `write_note(note: "daily")` instead of `type: "daily"` (5 of 5 in the final run). The extra prompt hint didn't change it. Both models assume "the daily note" is a note you name.
- **Thinking low made the 8B worse.** It passed 0 of 5, mostly by giving `read_note` the argument `note` (`write_note`'s name for it), and it took about 50% longer (81 s median against 55 s).
- **The repair round (send `check_plan`'s problems back, ask for the plan again) didn't fix anything** on either model. The same problems came back.

## The other model families

The same test was run on the other five families Nuru has a Modelfile for. It used the final planner (C plus the two hints), thinking off, and 5 plans each. Each family's model was built from its own Modelfile, so it planned with its tuned sampling settings; the planner's own system prompt replaces the Modelfile's.

| Planner | Base | Strict pass | Pass if `note: "daily"` counted | Median time |
|---|---|---|---|---|
| **qwen3** | qwen3:8b | **5/5** | 5/5 | **11 s** |
| **mistral-nemo** | mistral-nemo:12b | **5/5** | 5/5 | 20 s |
| qwen3.5 | qwen3.5:9b | 3/5 | 4/5 | 21 s |
| gemma4 | gemma4:12b | 0/5 | 2/5 | 29 s |
| llama3.1 | llama3.1:8b | 0/5 | 2/5 | 18 s |
| granite4.2 (for comparison) | granite4.2:8b | 0/5 | 4/5 | 53 s |
| granite4.2 (for comparison) | granite4.2:3b | 0/5 | 0/5 | 26 s |

- **Qwen3 8B is the best planner here.** It passed 5/5 at about 10 s per plan once loaded, five times faster than Granite 8B. Every plan was the minimal six steps: search → pick → read → draft the expression → calculate → write the daily note. Its one flaw is passing `note: "daily"` alongside `type: "daily"`. The tool says to leave `note` out, so it should be checked whether the server tolerates both.
- **Mistral Nemo's plans were the cleanest:** 5/5 and the same six steps, with `write_note {type: "daily", content: "Grocery total: $s5"}` and no `note`, exactly as the tool describes. It's slower (20 s) and larger (12B).
- **Qwen3.5** passed 3/5. In one failure it did the sum inside a `draft` step and never called `calculate`. In the other it wrote `note: "daily"`.
- **Llama 3.1** searches for the daily note (`search "daily note"` → pick → write) instead of using `type: "daily"`. It also makes up references like `$s3.lines[0].text`.
- **Gemma 4** stops after the draft, adds stray steps (`s3_1`, a vault-wide `find_in_notes`), and once gave `read_note` a `query`.

**Three small models, added later the same day.** Same test (thinking off, 5 plans each):
- `qwen3.5:0.8b` and `qwen3.5:2b` build from the existing `qwen3.5.Modelfile`, because the family is the name without its `:tag`.
- `lfm2.5-thinking:1.2b` got a new [lfm2.5-thinking.Modelfile](../modelfiles/lfm2.5-thinking.Modelfile): Liquid's shipped sampling settings (temperature 0.05, top_k 50) and the same system prompt as the other non-Granite families.

| Planner | Base | Strict pass | Pass if `note: "daily"` counted | Median time |
|---|---|---|---|---|
| lfm2.5-thinking | lfm2.5-thinking:1.2b | 0/5 | 0/5 | 4 s |
| qwen3.5 | qwen3.5:2b | 0/5 | 0/5 | 7 s |
| qwen3.5 | qwen3.5:0.8b | 0/5 | 0/5 | 2 s |
| qwen3.5 (added after) | qwen3.5:4b | **2/5** | 2/5 | 10 s |

None of the three can plan this request. They're fast, but each fails in its own way:
- **LFM2.5 1.2B copies the prompt's example instead of planning the request.** Its plans begin `search "Dana"` → pick "Dana's person note", then read a note called `daily_note`. In the runs that went further, `calculate` got the prose "sum of grocery amounts".
- **Qwen3.5 0.8B usually stops after the `search`.** In one plan it made up the budget figures ("Groceries: $45.75, $62.50, $11.99") and wrote them straight into the note. That's the made-up data a plan is meant to rule out.
- **Qwen3.5 2B gets the start right** (search → pick → read → draft), then loses the thread. It writes to `note: "$s5"` from within step s5 itself, takes part of a result (`$sN.field`), or stops after the search.

**Qwen3.5 4B, tested afterwards, is the first model under 8B to pass.** Two of its five plans are the ideal six steps. All five get search → pick → read → draft the expression → calculate right; every failure is in the daily-note write:
- two plans find today's daily note with `browse {type: "daily", limit: 1}` → pick. That works only if today's note already exists.
- one has no `note` and no `type`.
- one ends its content with a broken `$`.

**Overall:** apart from Qwen3.5 4B, below roughly 8B parameters, no model here held the plan format and the tool details together. Granite 8B nearly did. Qwen3 8B and Mistral Nemo 12B did every time.

**No Modelfile was changed for the earlier five families.** The planner sends its own system prompt and `num_ctx`, so a Modelfile only contributes sampling settings. The earlier test showed that putting plan instructions in the Modelfile doesn't work while tools are offered. Gemma 4 and Qwen3.5 sample at temperature 1.0, their vendors' presets, which may cost them some consistency. A lower temperature just for planning could be tried, but lowering it in their Modelfiles would also change normal Nuru on those models, so I left it.

## A harness bug found on the way: Ollama sorts schema keys

Ollama sorts the keys of a `format` schema alphabetically and makes the model write them in that order. I confirmed it with a two-key schema: `{zebra, apple}` came back as `apple, zebra`.

The first schema used `{id, tool, args}`. Sorted, that's `args, id, tool`, so a tool step had to *start* with `"args"`. The model always started with `"id"`, which ruled out tool steps entirely. All 5 first-run plans were `draft` steps only (a correct outline in prose).

Renaming the keys so the alphabetical order is the natural one fixed it:
- tool step: `step, tool, with`
- think step: `step, think, uses, want`

**This matters beyond plan mode.** [forced_schema.py](../src/core/forced_schema.py) and [write_args.py](../src/core/write_args.py) also force schemas, so their arguments are written in sorted order too. That's harmless for independent arguments. But any schema whose keys are meant to be written in a sequence (a reason before a verdict, say) is reordered silently.

## Results

| Approach | Planner | Think | Strict pass | Pass if `note: "daily"` counted | Median time |
|---|---|---|---|---|---|
| A: forced schema, no tools | 3B | off | 0/5 | 0/5 | 2 s |
| A | 3B | low | 0/5 | 0/5 | 10 s |
| B: A + full tool descriptions, code checks, one repair, harness-added answer step | 3B | off | 0/5 | 0/5 | 11 s |
| B | 3B | low | 0/5 | 0/5 | 19 s |
| C: B with the 8B as planner | 8B | off | **1/5** | 2/5 | 55 s |
| C | 8B | low | 0/5 | 1/5 | 81 s |
| C + two prompt hints (daily note; `read_note` takes `name`) | 8B | off | 0/5 | **4/5** | 53 s |
| C + two prompt hints | 3B | off | 0/5 | 0/5 | 26 s |

A's rows are the reruns after the key-order fix. Before the fix, A at thinking off went 0/5 with draft-only plans. Times include the repair call when there was one.

**The passing 8B plan** (C, thinking off, run 2):

```
s1 search {query: "March budget"}
s2 pick   uses s1  "path or title of the March budget note"
s3 read_note {name: "$s2"}
s4 find_in_notes {pattern: "grocery"}
s5 pick   uses s3, s4  "list of grocery amounts from the March budget note"
s6 calculate {expression: "$s5"}
s7 write_note {note: "daily", type: "daily", content: "$s6", mode: "append"}
s8 answer (added by the harness)
```

Even this plan has two weak points:
- It hands `calculate` a picked *list*, not a drafted expression.
- The rubric doesn't check s4, a vault-wide `find_in_notes` for "grocery". It's redundant once the note has been read, and it shows up in most 8B plans.

**A typical 3B plan** (B, thinking off): `search → read_note {note: "$s1"} → find_in_notes → calculate {expression: "sum of grocery amounts"}`, then it stops. It has the wrong argument name, no pick, a prose expression, and no write.

## What follows

1. **Use a different model to plan, not Granite 3B.** The 3B can't hold the plan format and the tool details together. The other families show that a local planner works: Qwen3 8B passed 5/5 at about 10 s, and Mistral Nemo 5/5 at about 20 s (see above). To try it: `NURU_PLANNER_MODEL=plan-qwen3`. Granite 3B could keep carrying out the steps. Before relying on this, test on more prompts than one, and check the memory cost of keeping two models loaded. Other options:
   - Claude through the existing expert path ([expert.py](../src/core/expert.py)), asked for this JSON instead of a numbered list. Only the request and tool definitions leave the machine.
   - A fine-tuned small planner, trained on plans from the 8B or Claude.
2. **Fix the daily-note mistake in the tool, not the prompt.** Every planner assumed you name the daily note; the hint didn't change that. If `write_note` accepted `note: "daily"` (or `"today"`) as today's daily note, 4 of 5 8B plans would pass.
3. **`check_plan` errors that keep coming back can be fixed by code instead of asking the model.**
   - `read_note {note: ...}` is unambiguous, so code can rename it to `name`.
   - A missing answer step is already added by the harness.
   - Asking the model again didn't work on either size.
4. **Avoid naming forbidden patterns in prompts.** The 3B used `$sN.path` far more often *after* a rule saying "there is no `$sN.field`". This fits the `check_claim` finding: small models do better with narrow, positive instructions.

## Side finding: `THINK=off` turns thinking on

[session.py](../src/core/session.py)'s `_parse_think` only recognises `"false"` as off. Any other word that isn't a level becomes `True`, and `.env.nuru` has `THINK=off`. The 16:54 run's log records `"think": true`: its first call thought for 88 s until the thinking watch stopped it as repeating. I haven't fixed this. Either `_parse_think` should also accept `off`, or `.env.nuru` should say `THINK=false`.

## What changed in the code

- **New [planner.py](../src/core/planner.py):**
  - the planner prompt (built from the live tool definitions)
  - the `format` schema
  - `check_plan`
  - `make_plan`, with one repair round and the harness's answer step
- **[session.py](../src/core/session.py):** a message starting with `PLAN:` goes to `_plan_turn`. That's one planning call with no tools offered, and nothing in the plan runs. The reply is the plan plus anything code found wrong with it. Two settings control it:
  - `NURU_PLANNER_MODEL`: default `MODEL`; `nuru-8b` was the best in this check
  - `NURU_PLANNER_THINK`: default `false`
- **Testing:** the existing 376 tests pass. I ran one `PLAN:` turn end to end through `Session.ask` with the 8B. It made two model calls and no tool calls, and returned a 9-step plan with the same `note: "daily"` mistake.
- **[granite4.2.Modelfile](../modelfiles/granite4.2.Modelfile)** still holds only the PLAN section from the earlier test. Plan mode no longer needs it, since the planner sends its own system prompt. Restore it with `git checkout nuru/modelfiles/granite4.2.Modelfile` before using Nuru normally.

The runner script and every plan as JSON are in the session scratchpad, not the repo.
