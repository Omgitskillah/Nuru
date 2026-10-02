# `statistics` tool check, 2026-10-02

This run checks whether Nuru uses the new `statistics` tool from `math-4-llms` (commit `cce2dba`), and how it splits work between `statistics` and `calculate`. It used two sets of 20 prompts:

- **Set A:** statistics only.
- **Set B:** statistics mixed with ordinary maths.

The earlier `calculate`-only run is in [calculate-tool-check-2026-10-02.md](calculate-tool-check-2026-10-02.md).

## Setup

- **Model and tools:** Nuru ran on `granite4.2:3b` with `THINK=low`, on branch `tools-purge`. `statistics` was added to `MODEL_TOOLS`, which makes 25 model tools.
- **What `statistics` does:** it takes 2 to 10,000 numbers and returns count, sum, mean, median, sample standard deviation, min and max.
- **Modelfile changes, rebuilt at startup:**
  - A routing line: "Mean, median, standard deviation, min, max or total of a list of numbers -> statistics".
  - A short `USING statistics` section.
  - The "average" example under `calculate` was swapped for a discount example, so the two sections don't contradict each other.
- **Run harness:** the same as the earlier run.
  - One fresh process per prompt, each with its own conversation database.
  - The scratch dummyVault and the semantic index were reset before every prompt.
  - Every prompt was a single turn.
  - Laya ran in shadow mode, and `ask_an_expert` was never called.
- **Time** is the turn itself; each process also took about 1.7 s to start.
- **Expected values** were computed with Python's `statistics` module.

## Summary

| | Set A: statistics | Set B: mixed |
|---|---|---|
| Correct | 17 of 20, plus 1 defensible alternative | 14 of 20 |
| Used `statistics` | 8 | 3 |
| Used `calculate` | 8 | 16 |
| Used no maths tool | 4 | 2 |
| Median time, inline prompts | 5.4 s | 12.3 s |
| Median time, vault prompts | 35.6 s | 57.2 s |
| Median time, all | 8.9 s | 16.6 s |

`statistics` never returned an error, and every call to it gave the right numbers. All but two of the wrong answers had the same cause: the model did a statistical step itself instead of handing it to a tool. The other two were a wrong formula (114) and a misread note (220).

1. **Medians are sorted in the model's head, even with `statistics` available.** For "median" prompts it sorted the list itself and used `calculate` only to average the two middle values, or used no tool at all. That is 6 prompts: 102, 107, 113, 118, 216 and 218.
   - The model got two of them wrong. On 113 it made up a value (1,150,000) that isn't in the list. On 218 it sorted 10 of the 12 unit costs into the wrong order.
   - The routing line names "median", so the model is ignoring it here.
2. **A standard deviation needed as a step on the way gets done by hand.**
   - The model used `statistics` when the prompt asked for the mean or SD itself: 101, 103, 104, 108, 109, 111, 117 and 217.
   - When the SD was only a step towards something else, it didn't reach for the tool:
     - **204, z-score:** it wrote the SD out in `calculate` around a mental mean of 81.2; the right mean is 80.
     - **208, standard error:** no tool, and the variance came out as 2.7 instead of 3.5.
     - **213, confidence interval:** it used `calculate` for the mean only, then guessed the SD as √3.5 instead of 2.
   - All three are wrong. 203 (coefficient of variation) is the one case that chained the tools correctly: `statistics`, then `calculate(sd / mean * 100)`.
3. **Copying values from a note is still where vault prompts fail.**
   - On 219 the model dropped one of the six readings (1490), so its mean was 5843.4 instead of 5117.83. It used `calculate`; with `statistics`, the returned count of 5 against 6 rows would have exposed the gap.
   - On 220 it called the sum of the task periods "CPU utilisation" (1.41 s). The right answer is 0.36.
4. **Anything the tool doesn't offer is handled from memory, with mixed results.** The mode (115) was right. Population SD (114) was wrong: the model called `statistics` and reported the sample SD, 2.138, as the population SD, which is 2.
5. **The calculator's error-and-hint path worked the one time it came up.** On 201 the model sent `** 2`, got `INVALID_SYNTAX`, and retried with `^ 2`. The answer (324) was right, though the explanation in the reply had the mean wrong.
6. **The trap questions in set B were handled well.** The harmonic average speed (211, 48 km/h) and the weighted grade (212, 87.1) were both right. Plain maths (205, 207, 209, 210, 214, 215) was always done with `calculate`, and was always right.

## Set A: statistics

| # | Kind | Prompt | Tool(s) | Nuru's answer | Expected | Correct? | Time |
|---|---|---|---|---|---|---|---|
| 101 | mean | Mean of 23, 29, 20, 32, 25 | statistics | 25.8 | 25.8 | yes | 26.4 s* |
| 102 | median, even count | Median of 7, 3, 9, 1, 12, 5 | calculate `(5 + 7) / 2` (sorted in its head) | 6 | 6 | yes | 8.5 s |
| 103 | sample SD | Sample SD of 2, 4, 4, 4, 5, 5, 7, 9 | statistics | 2.138 | 2.138 | yes | 5.4 s |
| 104 | mean + SD in words | Five run times: average, and how consistent? | statistics | 30.9 min, SD 0.85 | 30.9, 0.851 | yes | 10.7 s |
| 105 | min / max | Highest and lowest of 88, 92, 79, 95, 83, 90 | none | 95 / 79 | 95 / 79 | yes | 1.8 s |
| 106 | sum | Add up five receipts | calculate | 67.70 | 67.70 | yes | 4.0 s |
| 107 | median with negatives | Median of -3, 7, 2, -8, 5, 0 | calculate `(0 + 2) / 2` (sorted in its head) | 1 | 1 | yes | 9.2 s |
| 108 | mean + median | Seven daily step counts | statistics | 9,346.14 / 9,310 | 9346.14 / 9310 | yes | 5.3 s |
| 109 | zero spread | SD of 5, 5, 5, 5 | statistics | 0 | 0 | yes | 5.2 s |
| 110 | single value | Sample SD of 42 | none | undefined for n = 1 | undefined (tool would return SAMPLE_TOO_SMALL) | yes | 4.0 s |
| 111 | full summary | Average, median and spread of eight test scores | statistics | 81.625 / 85 / 8.96 | 81.625 / 85 / 8.959 | yes | 9.7 s |
| 112 | tiny decimals | Average of 0.0012, 0.0015, 0.0011, 0.0014 | calculate | 0.0013 | 0.0013 | yes | 4.5 s |
| 113 | large values | Average and median of four house prices | calculate ×2 | 1,191,250 / **1,065,000** | 1,191,250 / 1,177,500 | **no**: median used an invented value | 18.2 s |
| 114 | population SD (not in tool) | Population SD of 2, 4, 4, 4, 5, 5, 7, 9 | statistics | **2.138** (the sample SD) | 2.0 | **no** | 6.6 s |
| 115 | mode (not in tool) | Mode of 3, 7, 3, 9, 7, 3 | none | 3 | 3 | yes | 2.2 s |
| 116 | vault: mean | Average sleep in the gym log, recomputed | semantic_search, calculate | 6.4375 ≈ 6.44 h | 6.4375 | yes | 55.1 s |
| 117 | vault: mean + SD | Mean and SD of the 25 °C ADC readings | search_notes, read_note, statistics | 5117.83 / 5022.99 | 5117.83 / 5023.0 | yes | 34.8 s |
| 118 | vault: median | Median stack high-water mark in the FreeRTOS note | search_notes, read_note (sorted in its head) | 112 | 112 | yes | 27.4 s |
| 119 | vault: filtered mean | Average deadlift weight in the gym log | search_notes, read_note, calculate | 94.375 kg | 94.375 | yes | 35.6 s |
| 120 | vault: mean of a column | Average unit cost in the home-lab inventory | search_notes, read_note, calculate | 0.23 EUR, weighted by quantity (234.945 / 1017) | 17.04 for a plain mean of unit costs (median 0.055) | defensible alternative: per part rather than per line, and its maths is right | 55.8 s |

\* Prompt 101 includes loading the model.

## Set B: mixed statistics and ordinary maths

| # | Kind | Prompt | Tool(s) | Nuru's answer | Expected | Correct? | Time |
|---|---|---|---|---|---|---|---|
| 201 | mean, then power | Mean of 4, 8, 15, 16, 23, 42, squared | calculate ×2 (`**` rejected, retried with `^`) | 324 | 324 | yes (the explanation's mean was wrong) | 24.0 s |
| 202 | range | Range of 88, 92, 79, 95, 83, 90 | statistics | 16 | 16 | yes | 14.5 s |
| 203 | coefficient of variation | CV % of 10, 12, 9, 11, 13 | statistics, then calculate | 14.37 % | 14.37 % | yes | 23.0 s |
| 204 | z-score | z of 95 among 70, 80, 85, 90, 75 (sample SD) | calculate (SD written out by hand, mean 81.2 from its head) | **1.721** | 1.897 | **no**: wrong mean | 12.4 s |
| 205 | plain percent | 30% of 450 | calculate | 135 | 135 | yes | 4.8 s |
| 206 | mean, then scale | Average of six months of spending, and a year at that rate | calculate (×12 in its head) | 435.83 / 5,230 | 435.83 / 5230 | yes | 18.7 s |
| 207 | plain roots | sqrt(2) × sqrt(8) | calculate | 4 | 4 | yes | 8.3 s |
| 208 | standard error | SE of the mean of 12, 15, 11, 14, 13, 16 | **none** | **0.67** | 0.764 | **no**: variance 2.7 instead of 3.5 | 25.6 s |
| 209 | target average | Score needed on the fourth test to average 88 | calculate | 97 | 97 | yes | 10.2 s |
| 210 | percent change | 1250 to 1480 | calculate | 18.4 % | 18.4 % | yes | 6.4 s |
| 211 | harmonic trap | 120 km at 60, then 120 km at 40: average speed | calculate | 48 km/h | 48 | yes | 12.1 s |
| 212 | weighted-mean trap | 85 (30%), 92 (50%), 78 (20%) | calculate | 87.1 | 87.1 | yes | 6.8 s |
| 213 | confidence interval | 95% CI for the mean of eight values | calculate (mean only; SD guessed) | **[48.7, 51.3]** | [48.61, 51.39] | **no**: SD 1.87 instead of 2 | 30.6 s |
| 214 | plain units | Seconds in a week | calculate | 604,800 | 604800 | yes | 5.9 s |
| 215 | plain geometry | Hypotenuse of legs 5 and 12 | calculate | 13 | 13 | yes | 6.2 s |
| 216 | median, then difference | Median of seven temperatures, and the peak above it | calculate (median from its head) | 15.8 / 2.6 | 15.8 / 2.6 | yes | 20.1 s |
| 217 | vault: stats + gap | Average sleep, its SD, and the shortfall against 7 h | search_notes, read_note, statistics | 6.44 / 1.27 / 0.56 h | 6.4375 / 1.266 / 0.5625 | yes | 40.9 s |
| 218 | vault: sum of products + median | Inventory total and median unit cost | semantic_search only | 234.95 (copied from the note) / **0.305** | 234.945 / 0.055 | **no**: median from a mis-sorted list of 10 of the 12 costs | 82.0 s |
| 219 | vault: mean, then ratio | Mean of the 25 °C ADC readings, and 200 ppm as a % of it | search_notes, read_note, calculate ×2 | **5843.4 / 241.3 %** | 5117.83 / 275.5 % | **no**: dropped one reading | 51.2 s |
| 220 | vault: two answers | Total CPU utilisation and mean WCET from the FreeRTOS table | search_notes, read_note, calculate | **"1.41 s"** / 8.76 ms | 0.36 / 8.76 | **half**: WCET right, utilisation wrong | 63.1 s |

## What would likely help

These are suggestions only; none of them was tried in this run.

- **Name `statistics` where a statistic is a step on the way.** For example: "If you need a mean, median or standard deviation as part of a bigger calculation (z-score, standard error, confidence interval, coefficient of variation), get it from statistics first, then pass it to calculate." Example 203 is the pattern to copy.
- **Tell the model not to sort.** For example: "Never sort numbers yourself; statistics gives the median." Both median errors came from sorting by hand.
- **Rely on the count check.** On 219, `statistics` would have returned count 5 against 6 rows. The Modelfile already asks for that check, but it only works if `statistics` is the tool that gets called.
- **Population SD isn't offered by the tool.** Either add it to the tool or leave it out of the prompt guidance; the model misreported the sample SD as the population SD.
