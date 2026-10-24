# Test 17 with multilingual embeddings, 2026-10-05

> **Historical record.** Plan mode was removed on 2026-10-24 (PR #37). The planner, executor, planner Modelfiles and `planner_check/` files this page links to no longer exist on main; they are at commit [7c2c345](https://github.com/Omgitskillah/Nuru/tree/7c2c3458657409a6be66fa88036c09f01cd7a500).

**The change:** the semantic index now uses `nomic-embed-text-v2-moe`, swapped in for `nomic-embed-text:v1.5` with no code change.
- **Model:** 475M parameters, F16, 768 dimensions (the same as v1.5), a 512-token input limit, about 100 languages.
- **Same task prefixes as v1.5,** so the server code didn't change.
- **The v1.5 index is kept, for reverting:**
  - The v2 index has its own file, `dummyVault/.obsidian-mcp-semantic-v2.sqlite3`.
  - The v1.5 index stays at `dummyVault/.obsidian-mcp-semantic.sqlite3`, plus a copy at `.obsidian-mcp-semantic.v1.5-backup-20261005.sqlite3`.
- **Config:** `.env.obsidian` gained `OBSIDIAN_EMBED_MODEL` and `OBSIDIAN_SEMANTIC_DB`. In `.env.nuru`, `OBSIDIAN_EMBED_MODEL` changed to v2, with the old line kept as a comment.
  - Both files were backed up first, as `.env.*.bak-20261005`. They're gitignored.
  - **To revert:** remove the two `.env.obsidian` lines and restore the `.env.nuru` line.
- **Re-indexing dummyVault** (40 notes) took 4 s. Suite batches build their own index from the same settings.

**Search, English query against the German note** `nuru/berlin-admin-chaos-notes.md`:

| Query | v1.5 rank | v2 rank |
|---|---|---|
| internet contract cancel | not in top 8 | **1** |
| internet contract cancellation | not in top 8 | 2 |
| internet contract cancellation deadline | not tested | **1** |
| When do I have to cancel my internet contract | not tested | **1** |

**Test 17,** one run per planner. Nuru was Qwen3.5 4B with thinking off; the expert was hidden from the model.

| Planner | Plan | Score | Turn time | Planner time |
|---|---|---|---|---|
| Claude Haiku 4.5 (thinking budget 1024) | search → pick → read → answer | **4/5** (was 1/5) | 38.4 s | 6.4 s |
| Claude Sonnet 5.5 (effort low) | search → pick → read → answer | **4/5** (was 1/5) | 23.7 s | 2.2 s |

- **Both runs found and read the right note.** Both gave the deadline (2026-12-31) and the 3-month notice.
- **The point both still miss is "days from today":** both said 103 days.
  - The note itself counts the days "ab 2026-09-19" (from 2026-09-19), and the answer took that count and that date as "today".
  - The executor's prompts don't give today's date, so the answer can't do better. On the run date, the right answer was 87 days (today was 2026-10-05).
- **Next fix:** put today's date into the executor's answer and draft prompts. A day count also needs either a `calculate` that takes dates or the answer step doing the subtraction.

## Six configurations, test 17 × 3, with today's date given to the executor

**Changes before this run:**
- **The date:** the executor's pick, draft and answer prompts now start with the same "[Current date and time: …]" line that Nuru's own turn sends ([executor.py](../src/core/executor.py), [session.py](../src/core/session.py) `run_plan`).
- **Hidden-tool plans:** a Qwen planner's plan that uses `ask_an_expert` (now hidden) is treated as invalid, and Nuru answers on its own.
- **Search:** all six configurations use the nomic v2 index.
- **Isolation:** the models were unloaded between configurations, so each memory reading belongs to one configuration.

**Memory:** the run sampled GPU memory, free memory and swap every ~0.5 s. GPU memory includes the embedding model, about 1 GB.

| # | Planner → Nuru | Scores | Median turn (first turn) | Median plan time | GPU memory median / peak | Free memory low | Swap growth |
|---|---|---|---|---|---|---|---|
| 1 | Haiku 4.5 → Qwen 4B | 1, 1, 4 | 28.6 s | 5.8 s | 4.9 / 7.9 GB | 20% | +0.7 GB |
| 2 | Sonnet 5.5 → Qwen 4B | 4, 4, 4 | 31.1 s | 2.5 s (one 13.8 s) | 4.9 / 7.9 GB | 18% | +0.5 GB |
| 3 | Qwen 4B → Qwen 4B | 1, 1, 1 | 40.4 s (54.6) | 2.7 s (first 19.4 s) | 4.9 / 8.0 GB | 16% | +0.5 GB |
| 4 | **Qwen 4B alone** | **5, 5, 5** | **25.2 s** (50.7) | n/a | 4.9 / 8.0 GB | 17% | +1.5 GB |
| 5 | Qwen 9B → Qwen 9B | 1, 1, 1 | 97.8 s (127.9) | 6.8 s (first 33.4 s) | 7.5 / 10.7 GB | **6%** | +2.1 GB |
| 6 | Qwen 9B alone | 4, 5, 5 | 40.5 s (78.9) | n/a | 7.3 / 10.4 GB | 10% | +2.2 GB |

**Plans**, the same three times unless noted:

| # | Plan | What went wrong |
|---|---|---|
| 1 | Haiku: search → pick a *date* → answer (×2); search → pick note → read → answer (×1) | the two plans without a read answered from search snippets |
| 2 | Sonnet: search → pick note → read → answer | nothing in the plan itself |
| 3 | Qwen 4B: search → pick "the cancellation deadline and number of days from today" → answer | never reads the note |
| 5 | Qwen 9B: search → pick note, then stops | no read and no answer step (the harness adds the answer) |

- **Qwen 4B on its own is the only configuration that scored 5/5 every time.** It searches, reads the note, and counts the days with `calculate`: 87 days, from 2026-10-05.
- **Once v2 search finds the note, test 17 stops being hard for Nuru on its own.**
- **Qwen 9B alone** got 4, 5, 5, but it's slower, and it drove free memory down to 10%.
- **The date fix works:** every planner configuration's reply now names today correctly (2026-10-05).
- **The plans still lose the day count.** None includes a `calculate` step, so the answer copies the note's own count ("103 days", counted from 2026-09-19) or does the subtraction itself (117 or 118 days). That's the point Sonnet loses in every run.
- **The step language struggles here:** it can't pass a date from a read note into an expression.
- **Qwen 9B as planner and Nuru** is the worst case for both accuracy and memory: free memory hit 6%, and swap grew by 2.1 GB in 6 minutes.
