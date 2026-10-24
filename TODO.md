# TODO

Work still to do on Nuru. This is the one list for people and agents.

**How to use it**
- Each item has a stable ID: `WP01-M4` for a work-package milestone, `R<n>` for a standing reminder, `F<n>` for a follow-up. Refer to items by ID in commits and PRs (e.g. `WP01-M4: safe installer`).
- `[ ]` open, `[~]` in progress, `[x]` done. Tick an item in the same PR that finishes it, and add the date and commit or PR, e.g. `(done 2026-10-12, #16)`.
- **Done when** is the exit check. Don't tick an item until it holds.
- Remove items once they are older than a month. Git history keeps them.
- Reminders (`R`) are for later. Don't start one unless someone asks.

## WP01: local Obsidian chat (completed milestones)

Plan: [nuru/docs/obsidian-remote/01-local-obsidian-chat.md](nuru/docs/obsidian-remote/01-local-obsidian-chat.md). Evidence goes in `nuru/evals/obsidian_remote/wp01-local-chat.md`.

- [x] **WP01-M1** Protocol v1 and streaming spike (#12)
- [x] **WP01-M2** `nuru-serve` lifecycle and persistence (#13)
- [x] **WP01-M3** Chat and approvals: service side (#14) and Obsidian plugin (#15)
- [x] **WP01-M4** Package and install: a reproducible plugin bundle, manifest and styles, a safe installer, and instructions for local pairing and uninstalling. (done 2026-10-11, #16)
  - Installer `plugins/obsidian/install.js`, install/pairing/upgrade/uninstall docs, version check, byte-identical builds, the lantern icon replacing the chat bubble. Manual run in real Obsidian passed 2026-10-11 (install, upgrade, uninstall; the icon in light and dark).
  - **Done when:** install, upgrade and unload work in a disposable Obsidian vault, with settings and notes intact.
  - **Extra work to list in the M4 PR description** (done alongside, outside M4's scope):
    - The plugin's waiting line: "Nuru is getting ready…" while `/v1/status` says the model is loading, then "Thinking…", with seconds (`lib/view.js`).
    - Chat layout: your messages as right-aligned bubbles, replies as Markdown, one font size with the input (`styles.css`).
    - Task format: bare `[ ] task` lines didn't render as checkboxes and were invisible to `note_info` todos. `write_note`'s description now gives the format, and `write_note` turns bare checkboxes into list items, outside code fences. This is in the obsidian-4-llms submodule (obsidian-4-llms#4, merged as 9a8f193 and pinned here).
    - Benchmark case 37 ("tasks as checkboxes") and the `writes` field for sequence cases that are meant to write (`nuru/evals/decision_layer/`).
    - Root README links TODO.md.
- [x] **WP01-M5** Acceptance and handoff: benchmark results, screenshots, known limits, a protocol compatibility matrix and the WP02 handoff. (done 2026-10-11, #18)
  - Test vault, Session/HTTP equivalence, the semantic-index fix, the benchmark (all gates pass), the live-model lane (direct 30/30, plugin 29/30 under rubric 1, the miss a rubric gap; 0 unauthorized writes), [wp01-handoff.md](nuru/docs/obsidian-remote/wp01-handoff.md), two review rounds on #18.
  - Manual acceptance in real Obsidian 1.14.4 passed: the No/Yes byte and receipt check with a real writer, quitting Obsidian mid-turn, screenshots in light and dark.
  - **Done when:** the plan's completion gates hold, real macOS acceptance is recorded, and the live-model lane has been run or explicitly deferred.

- [x] **WP01-ACT** Show more clearly what the model is doing, in the plugin, from what protocol v1 already sends (ui-state's e107dc0 did this for the Qt window): tool arguments and why a call failed, retries and steers, a collapsible Thinking section, a live step line, a status line from `/v1/status`.
  - Done in code (branch `wp01-act-activity`): `lib/activity.js`, the steps in the panel, the live line, the status line, `scenario:activity` in `scripted_serve.py`.
  - Checked in real Obsidian, light and dark, on 2026-10-11 with `scenario:activity` and the other scripted scenarios.
  - A real model turn checked the same day (dark).
  - History turn checked the same day: reopened from the picker, **Show all steps** brought every step back.
  - **Done when:** a live turn and a history turn show their steps in real Obsidian, and the line and status line read right.
- [ ] **WP01-ACT2** Later, if wanted: ui-state's data that v1 doesn't carry (thinking against the watch's budget, step against the step cap, turns Nuru ended itself, Laya's view, model/thinking level/expert budget in the status). Needs a protocol change.

## Now: WP02; next WP03

- [ ] **WP02** Remote chat and note capture, milestones M1–M5. Plan: [02-remote-chat-and-capture.md](nuru/docs/obsidian-remote/02-remote-chat-and-capture.md)
  - [x] **WP02-M1** Remote contract and migration: pin legacy capture sources, define device scopes, retries and schema mapping, and freeze compatibility and denial fixtures. [Contract](nuru/docs/obsidian-remote/wp02-m1-contract.md). (done 2026-10-12, PR #19) **Done when:** old request/response fixtures and the capability matrix pass model-free tests.
  - [x] **WP02-M2** Reliable direct capture and receipt reconciliation. (done 2026-10-12, PR #20) The local service now writes exact Inbox captures without a model, reserves request identity and intent durably, returns immutable receipts, and exposes device-owned status. Model-free parallel, HTTP and process-crash checks cover retry and uncertain outcomes. **Done when:** duplicate, race and process-crash tests prove one note per request ID with all model providers disabled.
  - [x] **WP02-M3** Pairing, scoped credentials and private exposure. (done 2026-10-17, PR #21) Implementation and model-free HTTP checks (2026-10-13), including review fixes for shared-history read scope, rate-limited human pairing codes and expiry guidance; [operator guide](nuru/docs/obsidian-remote/wp02-m3-private-access.md). Real private HTTPS cutover, rollback to the RemoteNotes receiver on 27124 and forward replay rehearsed on the host on 2026-10-17 with a scratch vault; the route was restored to 27124 afterwards ([evidence](nuru/evals/obsidian_remote/wp02-remote-chat-capture.md)). **Done when:** HTTP and real private HTTPS checks reject invalid/revoked credentials before any vault access, with migration and rollback tested.
  - [ ] **WP02-M4** Phone chat and request-aware shortcuts. **Done when:** browser fault tests and real iPhone/Android acceptance cover resume, approvals and captures.
  - [ ] **WP02-M5** Opt-in macOS operation and release. **Done when:** model-free restart/recovery checks, release evidence and authorized login/reboot acceptance (or an explicit incomplete gate) are recorded.
- [ ] **WP03** Vault maintenance reports and review in Nuru. Plan: [03-vault-maintenance-ui.md](nuru/docs/obsidian-remote/03-vault-maintenance-ui.md)

## Follow-ups (F)

- [ ] **F1** Test 32's remaining model reasoning failure: it treats periodic attach as dependent on PSM. The Sonnet reference check, verdict-aware scorer and saved-batch rescoring were completed 2026-10-06 (#4); [the evidence](nuru/evals/test32-verdict-2026-10-06.md) shows the test is answerable. Evaluate claim checking (R7) or expert verification. **Done when:** the model gives the correct verdict without a scorer false positive on fresh runs.
- [ ] **F2** Repeat the original 34-test Qwen3.5 4B suite × 5 on current code for a better variance estimate, recording model, fixture and scorer hashes. The 2026-10-06 [34 × 3 baseline](nuru/evals/results-2026-10-06-full-baseline.md) already includes run_python, line fit, the new scorer and the step-cap answer; run cases 35–37 separately so the original /170 comparison stays intact.
- [ ] **F3** Finish write intent and verified target provenance for test 31. Error normalization shipped in [PR #5](nuru/evals/validated-dispatch-pr3-2026-10-06.md); independent mutation scope and target provenance did not. **Done when:** ambiguous create-versus-append requests and wrong-target writes are refused or clarified with Laya disabled as well as enabled.
- [ ] **F4** Recheck the expert planner's `BadRequestError` (`core/expert.py`) before turning `at_step_cap` escalation back on.
- [ ] **F5** Re-index the real vault with nomic v2. Check chunk truncation: 512-token limit vs 1,500-character chunks.
- [ ] **F6** Run correction prompts 901–907 (`nuru/evals/tool_checks/prompts_corrections.json`).
- [ ] **F7** Calibrate `note_type`.
- [ ] **F8** Fix model habits: false "done" claims, mixing up created and appended, moving notes out of the archive.
- [ ] **F9** A reply with text before and after a tool call glues the pieces without a space, sometimes repeating the answer (1 turn in 5 to 1 in 8 in the WP01 live lane runs, direct and plugin alike). Seen in `nuru/evals/obsidian_remote/results-2026-10-11-m5/live-runs.jsonl.gz`.
- [ ] **F10** Remove Laya's remaining code. Since its `laya` dependency became the optional `model` extra (about 720 MB, mostly torch), what's left is kilobytes and never runs while every Laya point is `off`. That covers `servers/laya` (server, predictor, decisions, question sets), Laya's hooks in `core/decision_shadow.py`, `core/label_cli.py` (`nuru label`/`export`), the Laya thresholds in `config/decision_layer.yaml`, and the Laya-only scripts in `nuru/evals/decision_layer/`. First move what the session uses with Laya off into `core`: `checks.check_write` (write_checks), the watch, step-cap and tier settings, and the decision log. **Done when:** `laya_mcp` is gone and write_checks, watch_thinking and step_cap behave the same.
- [ ] **F11** Remove the Opus expert planner (`core/expert.py`, `ExpertSettings`, the expert columns of the decision log) if it stays off. It has been off since 2026-10-05 (`at_step_cap: false`); see F4.

## Standing reminders (R)

- [x] **R0** Tools purge: 27 model tools merged down to 11 (`core/tool_contract.py` `MODEL_TOOLS`).
- [ ] **R1** Laya labels and fine-tuning. The v4 categoriser checkpoint needs a fresh test set and a regression check of the other question sets.
- [ ] **R2** Speed: thinking budgets vs timeouts (on the 8B, test 7 timed out at 15 minutes).
- [ ] **R4** Plan-then-act as a setting, off by default: one schema call `{"steps": [...]}` pinned as a checklist.
- [ ] **R5** Richer calculation tools. `calculate` and `statistics` exist; still to add: sum of products and unit conversion, if tests need them.
- [ ] **R6** Narrow the tools per request: classify the prompt, offer a per-family tool subset, and keep a full-list fallback.
- [ ] **R7** Enforce `check_claim` once its threshold is calibrated (caught 11 of 16, with 0 false alarms, in Phase 5).
- [ ] **R8** `assess_effort` is parked (off, not deleted). Revisit once Laya is trained.
- [ ] **R9** Use the expert more on the tests that never pass (2, 9, 13, 20, 21, 22, 25, 27).
- [ ] **R10** Parallel test runs with `OLLAMA_NUM_PARALLEL=2` and split test ranges.
- [ ] **R12** Embedding cost: does the indexer need a pass every 30 s (`vault_indexer.INTERVAL_SECONDS`)? Can the embedding model be unloaded between uses?
- [ ] **R13** Design proposal: router → evidence compiler → small-model draft → verification.
- [ ] **R14** History panel extras: delete and rename, search, backfill titles, stop creating empty conversations. Port it onto main fresh; `ui-state` (7d526ff) is parked.
- [ ] **R15** Broken links as a system function that runs after renames and moves, not a model tool.
- [ ] **R16** Make Qwen3.5 4B a good planner. In order:
  1. a planning test set
  2. profile sampling in plan mode
  3. per-tool argument schemas
  4. a `daily` target
  5. code fix-ups
  6. safety rules
  7. fine-tune only if 1–6 fall short
- [ ] **R17** A separate date-time tool, only if `run_python`'s `datetime` isn't enough. (Python tool done 2026-10-05.)
- [ ] **R18** A log of the vault notes sent to the expert: what, when, conversation and turn, model. Viewable in the UI.
- [ ] **R19** UI improvements. Start with source chips under replies, a "Changed N notes" card and a "Sent to expert" badge. Later: a `[[` note picker, regenerate/edit, and thumbs up/down saved as Laya labels. The Obsidian status line was completed in WP01-ACT.
- [ ] **R20** Run on macOS, Windows and Linux.
  - Known gaps:
    - the Obsidian mutations server uses `fcntl`, `dir_fd` and `O_NOFOLLOW`
    - CI is Ubuntu-only
    - the CLI's stdin answer timeout
    - Qt fonts
    - `caffeinate` in eval instructions
    - the Obsidian plugin's token path
  - Audit the Ollama start and stop, subprocesses and Docker, and the `.env`, log and DB paths.
- [ ] **R21** **The expert as the main model, chosen in config.** *Built 2026-10-16 (branch `claude-main-model`): `NURU_MAIN_MODEL=anthropic:claude-haiku-5-5` / `claude-sonnet-5-5` through the shared `packages/claude-access`, with prompt caching, kept thinking blocks, a local log of every request, `--allow-cloud` and a per-round token summary in the evals; `ask_an_expert` removed. Still open from the list below: the privacy rules (R25), a UI marker, falling back to the local model on an outage, and a cost cap.* One setting (e.g. `NURU_MAIN_MODEL=anthropic:<model>` next to `NURU_BASE_MODEL`, or a profile in `nuru/modelfiles/profiles.yaml`) runs Nuru's own turn loop on the Anthropic API instead of Ollama.
  - **Keep everything else:** the same `MODEL_TOOLS` through the same dispatch, write checks and step cap, ask-user questions, the conversation store, `nuru-serve` and the Obsidian plugin. Today Ollama is hard-wired (`core/providers/ollama.py`, `session.BASE_MODEL`), and the expert only plans (`core/expert.py`) and handles step-cap escalation.
  - **Work out:**
    - **Provider interface:** a provider layer that maps Ollama and Anthropic tool calls, streaming and thinking levels (off/low/high → effort).
    - **Privacy:** every note the model reads leaves the machine. Make it opt-in, show it clearly in the UI, and log it (R18).
    - **Cost:** reuse the daily expert budget, cache the system prompt and tool list, and cap tokens per turn.
    - **Outages and rate limits:** fall back to the local model or stop with a clear message.
    - **Expert-only paths:** turn off the expert planner and step-cap escalation, or point them at a stronger model.
    - **Local-model tuning:** Laya, the thinking watch and `check_claim` are tuned on local models. Check they still help.
    - **Prompt:** the Modelfile system prompt needs an API-side equivalent.
    - **Context budgets:** they assume a 16k local window.
  - **Done when:** the setting switches models with no code edits, the full eval suite runs on it as a profile with a recorded score, and the existing tests pass for both providers.
- [ ] **R22** **`browse` should show its "hidden" files.** It leaves out `Templates/` and `trash/` without saying so, so the model can't know they exist. On test 1 the dummy vault has 51 notes, 11 of them in `Templates/`; `browse` reports 40, so every model answers 40 files. Have `browse` at least say what it skipped (e.g. "11 more in Templates/, not listed"), or take an option to include them. Then revisit test 1's "51 files" check.
- [ ] **R23** **Re-evaluate Granite, accepting reasonable answers.** Some tests mark a defensible answer wrong: 1 (`browse` hides Templates, see R22), 8 (population SD 0.18 vs sample 0.19), 13 (576 B minimum vs 1024 B), 14 (9 vs 10 failures, depending on where n starts), 18, 19, 20, 21, 23 (255-byte capacity as a design choice), 27 (a playful parsec conversion) and 32 (the PSM answer key). Widen those checks to accept both readings, the way test 17 accepts ±1 day, then re-score the existing Granite q4/q8 batches (`.runs/suite-bench4-granite-*`), and Qwen's too so the comparison stays fair, before deciding between them.
- [ ] **R24** **Finer control of the prompt cache.** Ollama 0.40.4 exposes almost none: MLX's prompt cache is capped by a fixed 8 GiB constant (`maxPagedOutBytes` in `mlxrunner/prefix_cache.go`), and nothing clears it short of unloading the model. Measured 10-14: MLX `qwen3.5:4b-mxfp8` grew to 15.5 GB and scored the same as GGUF q8 (140.7 vs 139.3, 173 vs 170 min), so GGUF q8 stays the default.
  1. **Inside Ollama first:** check whether the `LLAMA_ARG_*` variables Ollama passes to `llama-server` (checkpoint count and spacing, and probably `--cache-ram`) change the cache test's numbers (`scratchpad cache_test.py`: new session ~252 tokens / 1.0 s, follow-up ~286 / 1.1 s on GGUF q8).
  2. **Then `llama-server` directly, as a small trial:** `--cache-ram` (cap), `--ctx-checkpoints` / `--checkpoint-min-step` (checkpoint placement), and `--slot-save-path` with `POST /slots/{id}?action=save|restore`: save the state after the system prompt and tools once, restore it at each session start. Check save/restore works with Qwen 3.5's hybrid layers. This needs a provider beside Ollama (shared with R21): OpenAI-style chat, `response_format` for schemas, `enable_thinking` in the template, the system prompt sent by Nuru, and Nuru starting the server on the GGUF.
  3. **MLX directly (`mlx_lm.server`) only if needed:** `--prompt-cache-bytes` / `--prompt-cache-size` cap it, but it needs MLX weights and a check of Qwen's tool calls there.
- [ ] **R25** **What Claude may see (sending rules).** With Claude as the main model every note a tool returns is sent to the API; for now it's evaluated as if it were a local model. Decide which notes stay local (e.g. `user/`, a `#private` tag, confidential content like MT09's), and whether such a result is withheld ("content withheld") or the turn goes to the local model; enforce it in code, test that protected text never appears in a request, and show a "cloud model" marker in the UI. The local request log (`nuru/.logs/claude-requests-<date>.jsonl`) already records what went out (part of R18).
- [ ] **R26** **One service for several vaults at once.** Today `nuru-serve` serves the one vault it started with, and the Obsidian plugin switches it between vaults. Serving several needs: conversations, devices and background jobs (semantic indexer, link auditor) tied to a vault; requests that name their vault; the queue shared across vaults (one model). Where each vault's data lives is under discussion on the board ("Keeping each vault's Nuru data with the vault").
