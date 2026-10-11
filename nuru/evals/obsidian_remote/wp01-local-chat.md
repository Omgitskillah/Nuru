# WP01 local Obsidian chat: evidence

Each claim is marked as one of: proposed, automated-tested, manually-tested or live-model-tested.

## M1: protocol and fixtures (2026-10-09, branch wp01-m1-protocol)

Machine: macOS 26.6.2, Obsidian installer 1.10.6 (app API 1.13.7), Python 3.14 (uv.lock), Node 24.21.0.

| Claim | Status | Evidence |
| --- | --- | --- |
| The v1 request, response and event shapes are frozen | automated-tested | `protocol-v1.schema.json`; 11 fixture streams and valid/invalid requests in `nuru/tests/fixtures/protocol_v1.json` |
| The fixture streams cover every event type and terminal state, and all three question outcomes | automated-tested | `test_fixtures_cover_every_event_type_and_terminal_state` |
| Broken streams are refused | automated-tested | `test_streams_that_break_the_rules_are_refused`: gaps, an event after the terminal, no terminal, a foreign turn, content before `started`, an unknown call, a tampered question, an open question at the end, unknown types and fields |
| A replay from any cursor shows nothing twice | automated-tested | `test_replay_from_every_cursor_shows_nothing_twice`, for every fixture and every cursor |
| A cursor past the stream asks for a resync and never starts a new turn | automated-tested | `test_a_cursor_past_the_stream_asks_for_a_resync_not_a_new_turn` |
| Request IDs are scoped to principal, vault and operation, and bound to their content | automated-tested | `test_request_ids_are_scoped_and_bound_to_their_content` |
| Every Session event type maps to a protocol event | automated-tested | `test_every_session_event_has_a_protocol_event` |
| The fake server refuses a missing token (401), a foreign Origin (403) and a rebinding Host (403), answers CORS preflight, and streams events as they are produced | manually-tested (curl) | Events arrived 0.2 s apart with `--delay 0.2` |
| An authenticated, streamed reply reaches a plugin in real Obsidian, and resumes after a drop | manually-tested | 2026-10-09 22:01 UTC, origin `app://obsidian.md`, `--delay 0.2`, `spike-result.json` `ok: true`. Without a token: a readable 401 (CORS on errors works). With one: status 200, first response in 3 ms. 4 events, then the reader was cancelled, then 8 more from `after=4`; seqs 1-12 in order, no duplicates. Arrival gaps 201-211 ms, so the stream was not buffered. Server-to-plugin latency 0-1 ms per event. `after=999` gave 410 `cursor_expired`. The final reply rendered as Markdown with `MarkdownRenderer` (wikilinks, emoji, code block) |

Suite: `uv run --locked pytest -q`, 1592 passed and 1 skipped when M1 merged (1522 before M1).

Dependencies:
- Python adds nothing new. `jsonschema` is already pinned in `uv.lock`.
- The spike plugin is plain CommonJS with no npm dependencies. The plugin's build toolchain
  and JS test runner come in M3, and get pinned then.

### Spike

```
uv run --locked python plugins/obsidian/spike/fake_server.py --delay 0.25
```

Then, in a disposable vault, enable `plugins/obsidian/spike/` as `nuru-spike`. The plugin writes
`spike-result.json` to the vault.

## M2: service lifecycle and persistence (2026-10-10, branch wp01-m2-service)

Machine: macOS 26.6.2, Python 3.14.7 (uv.lock). No model was loaded and no vault was written.

| Claim | Status | Evidence |
| --- | --- | --- |
| `nuru-serve` starts without loading a model, and status works with none loaded | automated-tested, manually-tested (curl) | `test_the_model_loads_on_the_first_turn_not_at_startup`; curl status said `chat: idle` |
| Host, Origin and token are checked before anything is read or run; a revoked token stops working | automated-tested, manually-tested (curl) | `test_refused_callers_reach_nothing`, `test_a_revoked_token_stops_working`; curl: 401 without a token, 403 for a foreign Origin |
| Another principal's conversations, turns, questions and requests are `not_found` | automated-tested | `test_another_principal_finds_nothing`, `test_answers_that_dont_fit_are_refused_and_change_nothing` |
| A retried request replays its result and never starts a second turn; the same ID for something else is a conflict; 8 simultaneous retries make one turn | automated-tested, manually-tested (curl) | `test_a_retried_submit_replays_the_turn_and_never_starts_another`, `test_a_reused_request_id_for_something_else_is_a_conflict`, `test_simultaneous_identical_retries_make_one_turn` |
| One shared Session, built on the first turn, switches conversation per turn; histories don't mix | automated-tested | `test_one_shared_session_switches_conversation_and_history_stays_apart` |
| Each conversation keeps its own decision-layer evidence (note IDs seen, last search) when the Session switches | automated-tested | `test_each_conversation_keeps_its_own_evidence_when_the_session_switches` (real Session, fake providers) |
| The notes server, the write ledger and status all use the service's one vault, whatever `.env.obsidian` says | automated-tested | `test_start_with_a_vault_gives_it_to_the_notes_server_and_the_write_ledger`, `test_the_served_vault_is_the_one_the_session_gets` |
| One process owns the service database; a second `nuru-serve` is refused before it migrates or recovers anything | automated-tested, manually-tested | `test_a_second_host_on_the_same_database_is_refused_and_changes_nothing`; a second real process on another port exited with "another nuru-serve is using …", and the first kept serving |
| A change that fails to store leaves memory as it was: the queue keeps working and an uncommitted answer isn't used | automated-tested | `test_a_cancel_that_fails_to_store_leaves_the_queue_working`, `test_an_answer_that_fails_to_commit_is_not_used_and_can_be_sent_again` (both fail without the fix) |
| A COMMIT that SQLite itself refuses is rolled back before memory is rebuilt, and leaves no replayable request; if even the rollback fails, the service takes no more work | automated-tested | `test_a_commit_refused_by_a_reader_is_rolled_back_and_memory_matches_the_store` (a real second connection holds a read, so COMMIT fails "database is locked"; fails without the fix), `test_once_unsafe_nothing_runs_or_is_read_and_a_restart_finds_only_what_committed` |
| Once a change can be neither committed nor rolled back, nothing touches the database: no request, read or event stream, no queued turn reaches Session, a waiting question is woken with No, an open stream ends without an error body, and the next start recovers only what committed | automated-tested | the three `test_once_unsafe_*` tests (a running turn with one queued, a turn waiting on a question, a reader already waiting) and `test_an_open_stream_ends_cleanly_when_the_database_becomes_unsafe`; each checks no SQL ran on the stuck connection and no worker exception; all four fail or hang without the fix |
| One generation at a time; a bounded queue says `queued`, refuses overflow with 429, and status answers while a turn runs | automated-tested | `test_turns_wait_their_turn_and_status_stays_responsive` |
| Questions: Yes, No, expiry on the host clock (taken as No), late, duplicate, foreign and wrong-operation answers | automated-tested | the four question tests in `test_service_host.py` |
| Stop while queued, while running and while a question waits; Stop on an ended turn changes nothing | automated-tested | the three `test_stop_*` tests |
| After a crash, unfinished turns end `failed` (`service_restarted`), open questions `expired`, and nothing is rerun; a retry of their request ID replays | automated-tested | `test_after_a_crash_unfinished_turns_end_and_are_never_rerun`: a real child process dies (`os._exit`) mid-question with a turn queued |
| Shutdown cancels queued and running turns and closes the Session without stopping Ollama; a port already in use exits cleanly and releases the database | automated-tested, manually-tested (SIGTERM, a taken port) | `test_shutdown_cancels_turns_in_hand_and_closes_without_stopping_ollama` |
| Over HTTP, a stream replays from every cursor, follows a live turn with keepalives, and a cursor past the end gets 410 with `resync` | automated-tested | `test_a_turn_over_http_streams_and_resumes_from_any_cursor`, `test_a_live_stream_follows_the_turn_with_keepalives` |
| After 30 days a turn keeps only its final display state | automated-tested | `test_old_turns_keep_their_final_state_after_their_events_are_dropped` |
| Service and conversation databases are separate but join with ATTACH | automated-tested | `test_migrations_run_once_and_the_two_databases_join` |
| Tokens: only a hash is stored; files 0600 in a 0700 directory | automated-tested | `test_token_commands_keep_only_a_hash_and_files_private` |
| CLI and Qt behaviour unchanged | automated-tested | full suite: 1638 passed, 1 skipped |

Not in M2:
- Tool results and write receipts (M3). Session yields no event for either yet, so a stream has
  `tool_start` but never `tool_result` or `write_receipt`. TODO in `TurnStream.session_event`.
- How attachments reach the model is provisional (M3, with the plugin). TODO in `Service._prompt`.
- Tool arguments too large for one event are not yet cut for display: such a turn would fail `internal`.
- A live-model run of the service (needs approval).

### Smoke test

```
export NURU_SERVICE_DIR=<scratch>/service NURU_DB_PATH=<scratch>/db/conversation.db VAULT_PATH=<scratch>/vault
uv run --locked nuru-serve token add obsidian
uv run --locked nuru-serve --port 27199
curl -H "Authorization: Bearer $(cat <scratch>/service/clients/obsidian.token)" http://127.0.0.1:27199/v1/status
```

## M3a: tool results, write receipts and a scripted server (2026-10-11, branch wp01-m3a-service)

No model was loaded and no vault was written.

| Claim | Status | Evidence |
| --- | --- | --- |
| Each shown tool call gets a `tool_result` and each write a `write_receipt` (`applied`, `refused`, `failed`, `uncertain`), matched by the call's own ID | automated-tested | `test_each_tool_gets_its_result_and_each_write_a_receipt_of_what_it_did`; Session: `test_each_shown_tool_call_reports_its_outcome_by_its_own_id` (also checks the ledger row carries the call ID and the changed file) |
| An applied receipt names the file the write changed, not the name it was asked by | automated-tested | `test_receipts_name_the_files_the_real_notes_server_changed`: the pinned Obsidian backend in a scratch vault, for a title, a daily, an ID, an archive move and a trash move |
| An outcome for a call never shown, or already reported, is never reported or taken by another call | automated-tested | `test_an_outcome_is_reported_only_for_its_own_shown_call` |
| An outcome that can't be stored stops the turn, and nothing more of the turn is stored before it, least of all its end; once stored, the turn ends `failed`. If it never can be, the turn stays unfinished and the next start recovers the receipt from the ledger. No write is retried | automated-tested | `test_an_outcome_that_cant_be_stored_stops_the_turn_and_is_never_taken_by_another_call`, `test_a_turn_never_ends_before_a_receipt_that_failed_to_store` (an uncertain write followed by Session's Failure), `test_while_a_receipt_still_cant_be_stored_the_turn_stays_open_and_a_restart_recovers_it`; the last two fail without the fix |
| After a crash, each write gets the receipt its own ledger entry shows: in flight is `uncertain` (still blocking), committed is `applied` with its file, rejected is `failed`; a queued turn never gets an earlier turn's write | automated-tested | `test_after_a_crash_each_write_gets_the_receipt_its_own_ledger_entry_shows` (3 cases), `test_after_a_crash_a_queued_turn_never_gets_an_earlier_turn_s_write`; real child processes that die at each point |
| Long tool arguments are cut for display, so a turn never fails for the size of its display | automated-tested | `test_a_huge_write_is_shown_cut_and_the_turn_still_completes` (400,000 characters), `test_shown_arguments_keep_short_values_and_cut_the_rest` |
| Attachments name the note; the model reads it with its tools | automated-tested | `test_attachments_are_named_in_the_message_and_cant_leave_the_vault` |
| `nuru/tests/scripted_serve.py` runs the real host and HTTP server over scripted scenarios, for the plugin's end-to-end tests | automated-tested | `test_the_scripted_server_runs_scenarios_end_to_end` |

## M3b: the Obsidian plugin (2026-10-11, branch wp01-m3b-plugin)

Machine: macOS 26.6.2, Node 24.21.0, npm 11.19.0, esbuild 0.28.2, jsdom 30.1.2 (package-lock.json).
No model was loaded and no real vault was written.

| Claim | Status | Evidence |
| --- | --- | --- |
| Every terminal state and every approval result, end to end against the real nuru-serve (scripted scenarios) | automated-tested | `npm run test:e2e` (`test/e2e/scenarios.test.js`): completed, failed, uncertain write, refused write; approval Yes, No, expired, Stop while asked, and a late answer refused; a choice with the user's own words; the model's own question; Stop while running and while queued |
| A dropped connection resumes from the last event shown, with nothing twice | automated-tested | e2e `a dropped connection picks up where it left off…`; unit `test/turns.test.js` (cursors 0, 3, 6) |
| The plugin's Display and vault ID are Python's | automated-tested | e2e `Python's vault ID and Display are the plugin's`: all 13 fixture streams |
| Raw HTML in a reply shows as text; scripts, event attributes and `javascript:`/`obsidian:`/`file:` links don't survive | automated-tested | `test/render.test.js`, `test/view.test.js`, with the real renderer (markdown-it) |
| Questions get a card with the host's countdown; answers send the question's operation; closing the panel stops following but neither answers nor cancels | automated-tested | `test/view.test.js` |
| A message carries only what was attached | automated-tested | `test/view.test.js` |
| No reply makes an element that could load anything, however its Markdown is formed: images and embeds render as links, raw HTML as text, and the HTML is built in an inert document before it reaches the panel | automated-tested | `test/render.test.js`, real renderer (markdown-it, images off, HTML off): unmatched backticks, an indented fence, unclosed and mixed fences, a reply cut off mid-image, reference images, embeds and raw `<img>`/`<picture>`, each with a MutationObserver over the panel element. A tracker pixel in real Obsidian is part of the manual run |
| A message is sent once: a second Enter (or a question's answer) while it's on its way does nothing | automated-tested | `test/view.test.js`, with the submit answer held back |
| A message whose answer was lost keeps its request IDs: sending again replays it, never a second turn; a changed message first asks `/v1/requests/{id}` whether the old one arrived; an answer cut off after its status is retried under the same ID | automated-tested | `test/view.test.js`, `test/client.test.js` |
| A history answer for a conversation left behind, or for a closed panel, is dropped and starts no stream | automated-tested | `test/view.test.js`, answers released out of order |
| A message sent from one conversation never touches another: a late answer leaves another conversation's draft and attachments alone; a lost message is retried only in the conversation it came from, and settling it never switches or mixes conversations | automated-tested | `test/view.test.js`, the reviewer's two cases with answers held back; both fail on the previous code. Also: what a message is (text, attachments, destination) is taken before anything is awaited, and opening another conversation or closing the panel while a lost message is looked up sends nothing (3 tests, the lookup held back; found and not found) |
| The reply shown is always the newest | automated-tested | `test/view.test.js` (rendering is synchronous) |
| The plugin says why it can't chat: no token, no service, refused token, another vault | automated-tested | `test/client.test.js` |
| A lost POST is retried with the same request ID | automated-tested | `test/client.test.js` |
| In real Obsidian: connect, stream a long answer, open a citation, tools and receipts, approve, refuse, let a question expire, Stop, reload the plugin and resume, hostile HTML, a remote image | manually-tested | 2026-10-11, Obsidian 1.10.6 on macOS 26.6.2, a scratch vault served by `scripted_serve.py` (port 27125, questions waiting 20 s). All 11 checks passed. The service's records match: `scenario:long` completed truncated; tools receipt `applied`; approval answered Yes (`applied`), timed out (`expired`, No, `refused`), answered No (`refused`); two `scenario:slow` turns `cancelled` by the user, one across a plugin off/on; `scenario:hostile` completed. A `![x](http://127.0.0.1:27199/pixel.png)` reply showed as a link, and the request logger on 27199 saw one request only, from Safari's engine (`AppleWebKit/605.1.15`, not Electron) when the tester clicked the link, which the plugin opens in the browser. The run found one bug first: the client called the global `fetch` as a method, which Obsidian's fetch refuses ("Illegal invocation"), so every request failed and the panel said Nuru wasn't running. Fixed, with a test; the checks above ran on the fixed build. Not explained: the first message after that fix showed nothing at first, although the service ran it to completion; it didn't recur, and the same panel code against the same server worked in Node |

The M1 spike (`plugins/obsidian/spike/`) is removed; its result stays recorded under M1.

## M4: package and install (2026-10-11, branch wp01-m4-package)

Machine: macOS 26.6.2, Node 24.21.0, npm 11.19.0, esbuild 0.28.2. No model, no real vault.

| Claim | Status | Evidence |
| --- | --- | --- |
| Install and upgrade keep the notes, the plugin's `data.json` and `community-plugins.json`; only `main.js`, `manifest.json` and `styles.css` change | automated-tested | `test/install.test.js` |
| The installer checks every path before writing: a missing build file or a vault that isn't a folder writes nothing; symlinked folders, dangling links and folders in a file's place are refused | automated-tested | `test/install.test.js` |
| A copy or a rename that fails part-way leaves the installed version and no staging folder (a first install leaves no plugin files); if the old files can't be put back either, they stay in the staging folder and the error names it | automated-tested | `test/install.test.js`, with `EPERM` injected on a rename |
| `node install.js <vault>` builds and installs into `<vault>/.obsidian/plugins/nuru/`; without a vault it prints its usage and exits 1 | automated-tested | run into a scratch vault |
| Two builds from the same commit are byte-identical; the build refuses a `manifest.json` version that differs from `package.json`'s | automated-tested | `dist/` built twice from clean: `main.js` 484436ec…ab59, `manifest.json` 0d97e746…eb727, `styles.css` 9f8a3059…1157 (SHA-256) |
| The ribbon and the sidebar tab show the Nuru lantern, in the theme's icon color | manually-tested | 2026-10-11, real Obsidian on macOS, light and dark themes: the lantern shows on the ribbon and the sidebar tab |
| A started turn with nothing to show says Nuru is getting ready while `/v1/status` says the model is loading (idle or loading), then "Thinking…", with the seconds so far; the line goes, and status checks stop, once the reply starts | automated-tested | `test/view.test.js` (2 tests) |
| The user's messages are right-aligned bubbles; replies, messages and the input share one font size | proposed | `styles.css`; to check in real Obsidian, light and dark |
| In real Obsidian: install into a disposable vault, enable, pair and chat; upgrade over it and reload; turn it off and delete the folder; settings and notes kept throughout | manually-tested | 2026-10-11, real Obsidian on macOS: install, enable, pair and chat; upgrade over it (installer again, plugin off and on) and uninstall (off, folder deleted) both worked. The first README steps for upgrade and uninstall were unclear and were rewritten as numbered steps with checks before this run |

## WP01-ACT: what the model is doing (2026-10-11, branch wp01-act-activity)

Plugin only, from what protocol v1 already sends: no protocol or service change. `Display` (and its Python
twin) is unchanged; the steps are a separate `Activity` (`lib/activity.js`).

| Claim | Status | Evidence |
| --- | --- | --- |
| A turn's steps come in order: each model call's thinking as its own block (the final reasoning replaces its streamed pieces), each tool call with its arguments, result and failure summary, each retry or steer | automated-tested | `test/activity.test.js` |
| A replay from any cursor, or an event applied twice, adds no step twice, on every fixture stream | automated-tested | `test/activity.test.js` |
| The panel shows thinking folded, each call named by its note/path/query with ✓/✗/…, and why it failed; a call's details list every argument; a step the user opened stays open as the turn goes on | automated-tested | `test/view.test.js` |
| The live line says what Nuru is doing now (getting ready, thinking, running a tool, writing the reply), with seconds and tool calls so far; seconds count from when the panel received the step | automated-tested | `test/view.test.js`, `test/activity.test.js` |
| A history turn shows its saved tools and last thinking; **Show all steps** replays its events from 0; events no longer kept say so | automated-tested | `test/view.test.js` |
| The status line shows the vault, model, notes and queue from `/v1/status`, refreshed every 10 s, in red when the model or notes are unavailable | automated-tested | `test/view.test.js` |
| `scenario:activity` streams thinking, a failed and a working call, a steer and a silent retry through the real service | automated-tested | run through `scripted_serve.start` and `/v1/turns/{id}/events` (2026-10-11) |
| All the steps sit in one section, open while the turn runs and folded to "Thought (N seconds)" (the service's clock) once it ends, unless the user opened or folded it; a history turn's section counts its steps | automated-tested | `test/view.test.js` |
| A write refused before it wrote reads "not written (rejected before writing; nothing changed)", and when a tool wrote more than once each receipt names its call; a call is named only by its note/path/query, never its content | automated-tested | `test/view.test.js`: the 2026-10-11 Giulia Bona turn (first `write_note` without `note`, `REJECTED_BEFORE_WRITE`; second created `nuru/giulia-bona.md`), which the panel showed as "write_note: failed" beside the created note |
| What each write did is listed at the end of the Thought section; under the reply, a "Vaulted" section (folds like Thought, starts open, shown only once a note was written) links each note a write created or updated (opening it in Obsidian), and an uncertain write stays visible there | automated-tested | `test/view.test.js` |
| In real Obsidian, light and dark: the steps, the live line and the status line | manually-tested | 2026-10-11, real Obsidian 1.13.7 on macOS, light and dark: `scenario:activity` through `scripted_serve.py`, and the other scripted scenarios; all read right. Then a real model turn (dark): "Look at my Trip and Budget notes, then write a short plan for the Rome trip in a new note called rome-plan...": search, two read_note and two write_note calls in order with thinking between, both receipts naming their call, Vaulted linking `nuru/rome-plan.md` (the link opened the note), status line "model ready · notes ready". Reopened from the picker, the same turn showed its history steps, and **Show all steps** brought back every step |

## M5: acceptance and handoff (2026-10-11, branch wp01-m5-acceptance)

Machine: macOS 26.6.2 (arm64), Python 3.14.7, Node 24.21.0. Code: 009d9eb plus this branch's uncommitted
changes (the live batch saved them and their hash, `diff_sha256` in `live-manifest.json`). Results, with
SHA-256 in `shasum -a 256 results-2026-10-11-m5/*`: [`results-2026-10-11-m5/`](results-2026-10-11-m5/).
Compatibility matrix, known limits and the WP02 handoff: [`wp01-handoff.md`](../../docs/obsidian-remote/wp01-handoff.md).

| Claim | Status | Evidence |
| --- | --- | --- |
| A disposable test vault, `NuruPluginTestVault`, is built from tracked definitions: `nuru/`, `shared/`, `user/`, Unicode names, duplicate basenames (`Plan.md` ×3), hostile HTML, a ~300 KB note, a note for a stale-revision edit, an archived note. Snapshots hash every file, and `changes()` gives the exact added/changed/removed set | automated-tested | `nuru/tests/plugin_test_vault.py`; `scripted_serve.py` and the plugin's e2e tests now run on it (8/8) |
| The same scripted turn through Session directly and through nuru-serve over HTTP (a real Session, the real notes server, only the model scripted) gives the same reply, the same tool calls in order with the same outcomes, the same stored history and the same vault: a Unicode read, a replace refused because the note changed on disk since it was read (the outside edit kept), a new note, and a shared write approved through a question | automated-tested | `test_direct_session_and_http_give_the_same_turn_and_the_same_vault`. File texts match except a new note's ULID and creation minute |
| `real_writer_serve.py`: nuru-serve with a real Session and the real notes server on the test vault, only the model scripted (it always adds a task to `shared/Plan.md`). No changes no file and gets a `refused` receipt; Yes changes exactly `shared/Plan.md` and gets an `applied` receipt naming it | automated-tested | `test_the_real_writer_server_writes_on_yes_and_leaves_the_vault_on_no` (a real process, with an absolute and a relative `--dir`). For the manual byte check: `scripted_serve.py` never writes (review finding) |
| The live harness gives each run a fresh copy of the pristine index in its own folder (the pristine one checkpointed once after it's built), so a killed run's WAL can't reach a later run | automated-tested | `test_each_run_gets_a_fresh_index_whatever_a_killed_run_left` (fails on the previous harness, which copied only the main file over a shared index) |
| The notes server dying mid-turn (SIGKILL between the read and the write) makes the write `uncertain`, ends the turn `failed`, retries nothing and leaves the vault byte for byte; status still answers | automated-tested | `test_a_notes_server_that_dies_mid_turn_leaves_its_write_uncertain_and_the_service_answering` |
| A served vault never gets another vault's semantic index. Before, the notes server (whose env Session builds) and the background indexer filled in `.env.obsidian`'s `OBSIDIAN_SEMANTIC_DB` for any vault, so a test vault's search could answer from the real vault's index, and the indexer could drop the real vault's notes from it. Now: the environment's `OBSIDIAN_SEMANTIC_DB`, else `.env.obsidian`'s only for its own vault, else `<vault>/.obsidian-mcp-semantic.sqlite3` | automated-tested | `test_a_served_vault_never_gets_another_vaults_semantic_index`, `test_another_vaults_index_is_never_used`; both fail without the fix. Proposed rule, for review in the PR |
| Performance gates, scripted (no model), 3 warmups then 30 turns per route, routes in alternating order. p95 acceptance: 6.1 ms (HTTP) and 7.6 ms (plugin), against ≤250. p95 overhead from Session yielding a chunk to the route having it: 2.8 ms over HTTP (received), and 87.7 ms in the plugin (in the panel's DOM), against ≤100. p95 Stop acknowledgement: 3.3 and 4.3 ms against ≤250 (the `cancelled` event arrives at 4.5 and 5.2 ms). p50 first event and completion: direct 7.6 / 145 ms, HTTP 14.1 / 170 ms; plugin first rendered text 93 ms, completion 180 ms | automated-tested | `bench_wp01.py`; `bench-summary.json`, `bench-raw.json.gz`. The plugin route sends from the real ChatView's composer and times each chunk when it shows in the panel's DOM, so it includes the view's render throttle (`RENDER_MS` = 80 ms), which uses most of the 100 ms budget. It runs on the jsdom stand-in over a real socket: Obsidian's own layout and paint are not included. The first published run (review finding) rendered outside ChatView and reported 5.7 ms |
| After 20 open/close cycles of the real ChatView (each: open, connect, send from the composer, wait for that new turn to end and its reply to render, close): 0 listeners and 0 intervals left (same as warm), the same active handles, heap after GC within 1%; the service has the same threads and RSS within 1% | automated-tested | `bench-summary.json` `leaks`. The first published run closed before the new turn ended (review finding) |
| Live-model lane: 6 frozen cases × 5 repetitions × 2 routes (Session directly; nuru-serve with the plugin's client). Qwen3.5 4B (digest `2a654d98…`), nomic-embed-text-v2-moe for the index, forwarded to the notes server in both routes. Thinking off, 16k context, the same vault bytes and index for every run, alternating order, and each run with its own conversation and service databases. **Direct 30/30, plugin 29/30; 0 unauthorized writes; 0 timeouts or crashes.** The scorer checks the reply, the exact files changed, the number of approval questions and each write's receipt. Every write case asked exactly once: Yes gave `applied shared/Plan.md`, and No gave `refused` with no change. The one failure is a rubric gap, not a wrong turn: a refused write whose reply said "I cannot add to shared/Team.md…", which the reply regex (`not`, `didn't`, …) doesn't accept. It is scored as a failure here (rubric version 1, as run). **Rescored under rubric version 2** (`live_cases.json`, after the PR #18 review: the refusal regex also accepts `cannot`, `can't` and `unable`), the same saved runs give direct 30/30 and plugin 30/30. That is a rescore, not a new run. The same review made a write applied without its required approval count as unauthorized, not only as a miss: 0 such writes in these runs (every write case asked exactly once). Median seconds to a correct result, direct vs plugin: read 9.5 / 8.3, search-and-answer 17.2 / 18.8, arithmetic 15.0 / 15.0, authorized write 16.5 / 17.0, refused shared write 17.3 / 16.9, follow-up (2 turns) 30.2 / 31.7 | live-model-tested | `live_wp01.py`, `live_cases.json`, scorer tests `test_live_wp01.py`; `live-summary.json`, `live-runs.jsonl.gz`, `live-manifest.json`. Direct times include Session start and model load in its own process. Plugin times include the service building its Session on the first turn. Replaces the first published run, which a review found used a weaker rubric, a shared service database, and the notes server's own embedding settings |
| Seen in the live replies, on both routes alike (4/35 direct, 5/35 plugin turns): when the model writes text before a tool call and again after, the reply is both pieces glued without a space ("…add the line to it.I cannot add…"), sometimes the answer twice. Session's behaviour, not the service's (F9) | live-model-tested | `live-runs.jsonl.gz` |
| CLI and Qt behaviour unchanged | automated-tested | full suite: 1682 passed, 1 skipped; plugin 64 unit + 8 e2e |

Commands:

```
uv run --locked pytest -q
(cd plugins/obsidian && npm test && npm run test:e2e)
uv run --locked python nuru/evals/obsidian_remote/bench_wp01.py            # ~1 min, no model
uv run --locked python nuru/evals/obsidian_remote/live_wp01.py --repeats 5 # ~25 min, Qwen3.5 4B; ask first
```

Still to do for M5 (manual, real Obsidian on macOS): see "M5 manual acceptance" below.

### M5 manual acceptance

Most of the plan's macOS acceptance ran in M3b, M4 and WP01-ACT (above). Left:

1. Record Obsidian's version (Settings → About) and macOS's.
2. `uv run --locked python nuru/tests/real_writer_serve.py --dir <scratch> --port 27126` (a real Session and
   the real notes server; only the model is scripted). Copy its token as in the plugin README's scripted-server
   steps, open `<scratch>/NuruPluginTestVault`, and keep the JSON line it prints.
3. Byte check, one answer at a time. Before each message, record the vault's hashes:
   `uv run --locked python -c "import json, sys; sys.path.insert(0, 'nuru/tests'); import plugin_test_vault as v; print(json.dumps(v.snapshot('<scratch>/NuruPluginTestVault')))" > before.json`.
   Send any message and answer **No**: the vault must be unchanged, and the receipt `refused`. Record again,
   send another and answer **Yes**: only `shared/Plan.md` may change (a `- [ ] buy tickets` line added), and the
   receipt must be `applied` naming it. Compare with `plugin_test_vault.changes(before, after)`.
4. With `scripted_serve.py` instead (it has `scenario:slow`): quit Obsidian (⌘Q) with a `scenario:slow` turn running. `curl -H "Authorization: Bearer <token>"
   http://127.0.0.1:27126/v1/status` still answers. Reopen Obsidian: the panel reconnects, and the turn is
   in the picker's history.
5. Screenshots, light and dark: a completed reply with a citation, an approval card, the Thought section
   with steps, the Vaulted section. Into `nuru/evals/obsidian_remote/screenshots/`.
