# `calculate` tool check, 2026-10-02

This run checks whether Nuru uses the new `calculate` tool. The tool comes from the `math-4-llms` submodule and replaces `add`, `subtract`, `multiply` and `divide`. The run used 20 prompts across a range of maths.

**Setup:** Nuru ran on `granite4.2:3b` with `THINK=low`, on branch `tools-purge`. The tools were the ones in `MODEL_TOOLS` after the swap, 24 of them. The model was rebuilt from the updated `granite4.2.Modelfile` at startup.

- Each prompt ran in a fresh process with its own conversation database, against a scratch copy of dummyVault.
- The vault and the semantic index were reset before every prompt.
- Every prompt was a single turn with no follow-up.
- Laya ran in shadow mode, the default.
- `ask_an_expert` was never called.

**Time** is the turn itself, from sending the prompt to the end of the reply. It leaves out startup, which was about 1.7 s per process (7 s for the first, which also built the model). Prompt 1 includes loading the model.

## Summary

- **`calculate` was used on 14 of 20 prompts.** All 14 calls were well-formed, and none came back with a calculator error. Every result the tool returned was correct.
- **The final number was right on 18 of 20 prompts.** The two misses: 18 refused, and 17 got the sum right but missed the note's contradiction. On 15, 19 and 20 the right figure was copied from the note rather than recomputed.
- **13 of the 14 standalone prompts used the tool.** Two answered without it:
  - 9 (sin 30°) is a fact the model knows by heart.
  - 12 (1 ÷ 0) was a correct refusal.
- **Vault prompts are the weak spot: 2 of 6 used the tool.** On 15, 19 and 20 the model read the note and repeated the note's own figure instead of recomputing it. The Modelfile says not to do that ("Never present the note's own result as your recomputation"). On 18 it didn't search at all and claimed the gym log doesn't exist.
- **Some arithmetic still happens in the model's head.** On 8 (standard deviation), the model worked out the squared deviations itself (2.25, 6.25 and so on) and only passed their sum to `calculate`. They were right this time, but this is exactly the gap a list-based `statistics` tool would close.
- **Median time:** 6.5 s for all 20 prompts, 5.8 s for the standalone ones and 23.4 s for the vault ones.

## Results

| # | Kind | Prompt | Used `calculate`? | Expression sent | Nuru's answer | Expected | Correct? | Time |
|---|---|---|---|---|---|---|---|---|
| 1 | percent | What is 17.5% of 2,340? | yes | `2340 * 0.175` | 409.5 | 409.5 | yes | 24.7 s |
| 2 | word problem | 186.40 EUR bill split four ways plus a 12% tip; how much each? | yes | `(186.40 * 1.12) / 4` | 52.19 EUR | 52.192 | yes | 6.5 s |
| 3 | root | Square root of 7569? | yes | `sqrt(7569)` | 87 | 87 | yes | 2.9 s |
| 4 | compound interest | 5000 EUR at 4.2% compounded yearly for 15 years? | yes | `5000 * (1 + 0.042) ^ 15` | ≈ 9,268.00 EUR | 9267.99… | yes | 5.3 s |
| 5 | unit conversion | 72 °F to Celsius | yes | `(72 - 32) * 5 / 9` | ≈ 22.22 °C | 22.22 | yes | 6.2 s |
| 6 | engineering | 3.7 V, 2500 mAh: how many Wh? | yes | `3.7 * (2500 / 1000)` | 9.25 Wh | 9.25 | yes | 10.1 s |
| 7 | mean | Average of 14, 22, 9, 31, 18 | yes | `(14 + 22 + 9 + 31 + 18) / 5` | 18.8 | 18.8 | yes | 3.8 s |
| 8 | standard deviation | Sample SD of 4, 8, 6, 5, 3, 7 | yes, partly | `sqrt((2.25 + 6.25 + 0.25 + 0.25 + 6.25 + 2.25) / 5)` | ≈ 1.871 | 1.8708 | yes (deviations worked out in its head) | 13.4 s |
| 9 | trig (degrees) | Sine of 30 degrees | **no** | none | 0.5 | 0.5 | yes (known fact) | 1.6 s |
| 10 | logarithm | log10 of 250000 | yes | `log10(250000)` | ≈ 5.3979 | 5.39794 | yes | 4.1 s |
| 11 | rate | 342 km on 27.5 L, in L/100 km | yes | `(27.5 * 100) / 342` | ≈ 8.04 | 8.0409 | yes | 9.1 s |
| 12 | division by zero | 1 divided by 0 | **no** | none | "undefined" | undefined | yes | 1.6 s |
| 13 | big integers | 123456789 × 987654321, exact | yes | `123456789 * 987654321` | 121932631112635269 | 121932631112635269 | yes | 3.6 s |
| 14 | geometry | Hypotenuse of legs 9 and 40 | yes | `sqrt(9*9 + 40*40)` | 41 | 41 | yes | 6.5 s |
| 15 | vault: mean | Average sleep in the gym log; check the note's figure | **no** | none (search_notes, read_note) | 6.44 h, copied from the note | 6.4375 (note's 6.44 is right) | yes, but not recomputed | 19.0 s |
| 16 | vault: sum of products | Recompute the home-lab inventory total | yes | `480*0.004 + 350*0.006 + … + 100*0.01` (all 11 rows) | 234.95 EUR | 234.945 | yes | 27.9 s |
| 17 | vault: inconsistent note | FreeRTOS utilisation from the table; matches the note? | yes | `(0.8/10) + (6.0/50) + (12.0/100) + (5.0/250) + (20.0/1000)` | 0.36, "the note does not state a total" | 0.36; the note says 0.58 | **half**: right sum, missed the contradiction | 47.5 s |
| 18 | vault: formula | Epley 1RM for the 95 kg deadlift in the gym log | **no** | none (no tool calls at all) | "I don't have your gym log" | 110.83 kg | **no** | 5.6 s |
| 19 | vault: verify chain | Is 13.6 years right for architecture B? | **no** | none (search_notes, read_note) | "matches the figure", copied from the note | 13.56 → 13.6 | right number, not verified | 16.5 s |
| 20 | vault: ratio | Verify the temperature coefficient at 100 ppm, in counts/°C and %/°C | **no** | none (search_notes, read_note) | 24.2 counts/°C, ≈ 0.32 %/°C, copied from the note | 24.2; 0.319 % | right numbers, not verified | 27.8 s |

## Observations

1. **The calculator's strict grammar caused no trouble.** The model always wrote explicit `*`, didn't use commas in numbers, and sent no units or prose. There were zero `INVALID_SYNTAX` or other calculator errors, so the retry-on-hint path was never exercised. Prompt 14 shows the model rewriting `9^2` as `9*9`, which was safe.
2. **Overly precise results are handled.** On 4, fend returned 45 decimal places and the model rounded to a sensible 9,268.00. On the `approx.` results (5, 8, 10, 11) the model kept the "approximately".
3. **When the tool is used doesn't depend on the tool.** For standalone questions the model reaches for `calculate` reliably. For "check the note" requests it treats reading the note as the job, and quotes the note's own working. This prompting gap is the same one the old four tools had. It is not a regression from the swap.
4. **Prompt 17 is a reasoning miss, not a tool miss.** The model computed 0.36 correctly, but said the note gives no total when it states U = 0.58. It never compared the two.
5. **Prompt 18 never searched the vault.** It's a routing failure: with "my gym log" in the prompt, the model should have searched before saying the log doesn't exist.

## Reproducing

The driver and runner live in the session scratchpad, not the repo:

- `driver.py` runs one prompt through `Session.start()` and `ask()`, and records every `MCPTools.call_tool_result`.
- `runner.py` starts a fresh process per prompt, resets the vault and the semantic index first, and stops any prompt that runs past 10 minutes.

Expected values were computed with the crate's own `math-tool` CLI.
