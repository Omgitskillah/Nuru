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
| A COMMIT that SQLite itself refuses is rolled back before memory is rebuilt, and leaves no replayable request; if even the rollback fails, the service takes no more work | automated-tested | `test_a_commit_refused_by_a_reader_is_rolled_back_and_memory_matches_the_store` (a real second connection holds a read, so COMMIT fails "database is locked"; fails without the fix), `test_a_change_that_cant_be_rolled_back_stops_the_service_taking_work` |
| One generation at a time; a bounded queue says `queued`, refuses overflow with 429, and status answers while a turn runs | automated-tested | `test_turns_wait_their_turn_and_status_stays_responsive` |
| Questions: Yes, No, expiry on the host clock (taken as No), late, duplicate, foreign and wrong-operation answers | automated-tested | the four question tests in `test_service_host.py` |
| Stop while queued, while running and while a question waits; Stop on an ended turn changes nothing | automated-tested | the three `test_stop_*` tests |
| After a crash, unfinished turns end `failed` (`service_restarted`), open questions `expired`, and nothing is rerun; a retry of their request ID replays | automated-tested | `test_after_a_crash_unfinished_turns_end_and_are_never_rerun`: a real child process dies (`os._exit`) mid-question with a turn queued |
| Shutdown cancels queued and running turns and closes the Session without stopping Ollama; a port already in use exits cleanly and releases the database | automated-tested, manually-tested (SIGTERM, a taken port) | `test_shutdown_cancels_turns_in_hand_and_closes_without_stopping_ollama` |
| Over HTTP, a stream replays from every cursor, follows a live turn with keepalives, and a cursor past the end gets 410 with `resync` | automated-tested | `test_a_turn_over_http_streams_and_resumes_from_any_cursor`, `test_a_live_stream_follows_the_turn_with_keepalives` |
| After 30 days a turn keeps only its final display state | automated-tested | `test_old_turns_keep_their_final_state_after_their_events_are_dropped` |
| Service and conversation databases are separate but join with ATTACH | automated-tested | `test_migrations_run_once_and_the_two_databases_join` |
| Tokens: only a hash is stored; files 0600 in a 0700 directory | automated-tested | `test_token_commands_keep_only_a_hash_and_files_private` |
| CLI and Qt behaviour unchanged | automated-tested | full suite: 1635 passed, 1 skipped |

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
