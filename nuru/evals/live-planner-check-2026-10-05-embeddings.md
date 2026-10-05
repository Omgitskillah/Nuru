# Test 17 with multilingual embeddings, 2026-10-05

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
