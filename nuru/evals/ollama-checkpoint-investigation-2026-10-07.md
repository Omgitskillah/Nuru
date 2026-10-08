# Ollama context checkpoints: what to investigate (2026-10-07)

The first model call of every session re-reads about **1,024 tokens** of a prompt it has mostly seen
before: **~4.5 s per session** (4,504 ms for 1,024 tokens at ~227 tok/s on 10-06). Only ~36 of those
tokens are new (the user's message). The goal is to find an Ollama setting that brings that down to the
~36 new tokens.

## What the log shows

Ollama 0.35.1, `qwen3.5:4b` (family `qwen35`, Q4_K_M), `~/.ollama/logs/server.log`, task 17791 on 10-06:

```
new prompt, n_ctx_slot = 16384, task.n_tokens = 6154
checking sim = 0.994 (6118/6154)                       <- 6,118 tokens match the cached prompt
checking checkpoint with [6149, 6149] against 6118...  <- too late: past the shared part
checking checkpoint with [5129, 5129] against 6118...
restored context checkpoint (... n_tokens = 5130)      <- nearest one before 6,118
prompt eval time = 4504.90 ms / 1024 tokens
created context checkpoint 4 of 32 (n_tokens = 5130)   <- 6154 - 1024
created context checkpoint 5 of 32 (n_tokens = 6150)   <- 6154 - 4
```

At load: `context checkpoints enabled, max = 32, min spacing = 8192`.

**Why it happens.** Qwen3.5 is a hybrid model: some layers carry a running state rather than a
per-token KV cache. That state can't be rolled back to an arbitrary token, only to a saved
checkpoint. Ollama (its llama.cpp server engine) appears to save checkpoints only at two points per
prompt: **1,024 tokens before the end** and **4 tokens before the end**. A new session's prompt shares
the system prompt and tool list (~6,118 tokens) with the previous session, then differs at the user's
message. The checkpoint 4 tokens before the end is past that point. So it falls back to the one 1,024
tokens before the end and re-reads from there.

Within one conversation this probably doesn't happen on follow-up turns: the previous call's
"4 before the end" checkpoint lies inside the shared part. Worth confirming (question 5).

## Questions to answer

1. **Can the checkpoint spacing or placement be configured in Ollama 0.35.1?** `min spacing = 8192`
   and `max = 32` look like llama.cpp server options. Look for:
   - an `OLLAMA_*` environment variable;
   - a Modelfile `PARAMETER`, or an `options` field in `/api/chat`, that sets them.

   A small spacing (e.g. 256) would leave a checkpoint within 256 tokens of any shared prefix. Check
   the memory cost: each checkpoint is ~50 MiB here.
2. **What sets the "1,024 before the end" offset?** Is it `n_ubatch` / `n_batch` (Ollama's
   `num_batch` Modelfile parameter)? Is it a fixed constant, or a separate llama.cpp option? If
   `num_batch` moves it, `num_batch 256` would cut the re-read to ~256 tokens (~1.1 s). Check whether
   it slows full prompt reads. The log shows `n_batch = 512` / `n_ubatch = 512` for some model load,
   but the checkpoint offset is 1,024, so these may be separate or for a different model.
3. **Can a checkpoint be requested at an exact position?** Look for a cache API, such as llama.cpp's
   slot save/restore or a `cache_prompt` / `keep` option, that keeps the state at the end of the
   system prompt + tools.
4. **Does a warm-up request create the checkpoint we need?** If Nuru sends one request at session
   start whose prompt is just system + tools, its "4 before the end" checkpoint would sit at
   ~6,114, inside every later session's shared part. Check:
   - whether Ollama processes a prompt-only request at all (`num_predict: 0`, or `/api/generate`
     with an empty prompt);
   - whether that request keeps its checkpoints.

   Nuru already waits ~10 s for Laya at startup, so this could hide inside that wait.
5. **Is this only the first call of a session?** Check release notes or issues for follow-up turns
   in one conversation re-reading too. If yes, the cause is different (the history replayed
   differently from what was sent).
6. **Is anything newer than 0.35.1 relevant?** Look for release notes or issues mentioning "context
   checkpoint", "hybrid", "Qwen3.5", "recurrent" or "SWA" cache reuse.

## What to bring back

- The exact names of any setting (env var, Modelfile parameter or API option) and where it's
  documented.
- Ollama versions that changed checkpoint behaviour.
- Any linked GitHub issue or PR on checkpoint placement for hybrid models.

I can then test each candidate in isolation: two sessions with the same prompt, comparing
`prompt eval ... tokens` on the second session's first call.
