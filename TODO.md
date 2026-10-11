# TODO

Work still to do on Nuru. This is the one list for people and agents.

**How to use it**
- Each item has a stable ID: `WP01-M4` for a work-package milestone, `R<n>` for a standing reminder, `F<n>` for a follow-up. Refer to items by ID in commits and PRs (e.g. `WP01-M4: safe installer`).
- `[ ]` open, `[~]` in progress, `[x]` done. Tick an item in the same PR that finishes it, and add the date and commit or PR, e.g. `(done 2026-10-12, #16)`.
- **Done when** is the exit check. Don't tick an item until it holds.
- Remove items once they are older than a month. Git history keeps them.
- Reminders (`R`) are for later. Don't start one unless someone asks.

## Now: WP01, local Obsidian chat

Plan: [nuru/docs/obsidian-remote/01-local-obsidian-chat.md](nuru/docs/obsidian-remote/01-local-obsidian-chat.md). Evidence goes in `nuru/evals/obsidian_remote/wp01-local-chat.md`.

- [x] **WP01-M1** Protocol v1 and streaming spike (#12)
- [x] **WP01-M2** `nuru-serve` lifecycle and persistence (#13)
- [x] **WP01-M3** Chat and approvals: service side (#14) and Obsidian plugin (#15)
- [~] **WP01-M4** Package and install: a reproducible plugin bundle, manifest and styles, a safe installer, and instructions for local pairing and uninstalling.
  - Done in code (branch `wp01-m4-package`): installer `plugins/obsidian/install.js`, install/pairing/upgrade/uninstall docs, version check, byte-identical builds, the lantern icon replacing the chat bubble.
  - Left: the manual run in real Obsidian (install, upgrade, unload; the icon in light and dark).
  - **Done when:** install, upgrade and unload work in a disposable Obsidian vault, with settings and notes intact.
  - **Extra work to list in the M4 PR description** (done alongside, outside M4's scope):
    - The plugin's waiting line: "Nuru is getting ready…" while `/v1/status` says the model is loading, then "Thinking…", with seconds (`lib/view.js`).
    - Chat layout: your messages as right-aligned bubbles, replies as Markdown, one font size with the input (`styles.css`).
    - Task format: bare `[ ] task` lines didn't render as checkboxes and were invisible to `note_info` todos. `write_note`'s description now gives the format, and `write_note` turns bare checkboxes into list items, outside code fences. This is in the obsidian-4-llms submodule, branch `write-note-task-format`; it needs its own PR there and a submodule bump here.
    - Benchmark case 37 ("tasks as checkboxes") and the `writes` field for sequence cases that are meant to write (`nuru/evals/decision_layer/`).
    - Root README links TODO.md.
- [ ] **WP01-M5** Acceptance and handoff: benchmark results, screenshots, known limits, a protocol compatibility matrix and the WP02 handoff.
  - **Done when:** the plan's completion gates hold, real macOS acceptance is recorded, and the live-model lane has been run or explicitly deferred.

- [ ] **WP01-ACT** Show more clearly what the model is doing, in the plugin, from what protocol v1 already sends (ui-state's e107dc0 did this for the Qt window): tool arguments and why a call failed, retries and steers, a collapsible Thinking section, a live step line, a status line from `/v1/status`.
- [ ] **WP01-ACT2** Later, if wanted: ui-state's data that v1 doesn't carry (thinking against the watch's budget, step against the step cap, turns Nuru ended itself, Laya's view, model/thinking level/expert budget in the status). Needs a protocol change.

## Next: WP02 and WP03

- [ ] **WP02** Remote chat and note capture, milestones M1–M5. Plan: [02-remote-chat-and-capture.md](nuru/docs/obsidian-remote/02-remote-chat-and-capture.md)
- [ ] **WP03** Vault maintenance reports and review in Nuru. Plan: [03-vault-maintenance-ui.md](nuru/docs/obsidian-remote/03-vault-maintenance-ui.md)

## Follow-ups (F)

- [ ] **F1** Test 32 (periodic attach doesn't need PSM).
  - Give Sonnet the three notes and the question directly. If it also says "No", the test or rubric is unclear. Otherwise fix it with claim checking (R7) or expert review.
  - Fix the scorer so it judges the overall verdict, then rescore the saved batches.
- [ ] **F2** Full-suite rerun of Qwen3.5 4B alone on current code (34 tests × 5). Every baseline predates run_python, line fit, the scorer changes and the step-cap answer.
- [ ] **F3** Engineer handoff task 3: error normalisation plus write intent (the path to fixing test 31).
- [ ] **F4** Recheck the expert planner's `BadRequestError` (`core/expert.py`) before turning `at_step_cap` escalation back on.
- [ ] **F5** Re-index the real vault with nomic v2. Check chunk truncation: 512-token limit vs 1,500-character chunks.
- [ ] **F6** Run correction prompts 901–907 (`nuru/evals/tool_checks/prompts_corrections.json`).
- [ ] **F7** Calibrate `note_type`.
- [ ] **F8** Fix model habits: false "done" claims, mixing up created and appended, moving notes out of the archive.

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
- [ ] **R19** UI improvements. Start with source chips under replies, a "Changed N notes" card and a "Sent to expert" badge. Later: a status line, a `[[` note picker, regenerate/edit, and thumbs up/down saved as Laya labels.
- [ ] **R20** Run on macOS, Windows and Linux.
  - Known gaps:
    - the Obsidian mutations server uses `fcntl`, `dir_fd` and `O_NOFOLLOW`
    - CI is Ubuntu-only
    - the CLI's stdin answer timeout
    - Qt fonts
    - `caffeinate` in eval instructions
    - the Obsidian plugin's token path
  - Audit the Ollama start and stop, subprocesses and Docker, and the `.env`, log and DB paths.
- [ ] **R21** **The expert as the main model, chosen in config.** One setting (e.g. `NURU_MAIN_MODEL=anthropic:<model>` next to `NURU_BASE_MODEL`, or a profile in `nuru/modelfiles/profiles.yaml`) runs Nuru's own turn loop on the Anthropic API instead of Ollama.
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
