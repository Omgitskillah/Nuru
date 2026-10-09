# WP01 local Obsidian chat: evidence

Each claim is marked as one of: proposed, automated-tested, manually-tested or live-model-tested.

## M1: protocol and fixtures (2026-10-09, branch wp01-m1-protocol)

Machine: macOS 26.6.2, Obsidian 1.10.6, Python 3.14 (uv.lock), Node 24.21.0.

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
| An authenticated, streamed reply reaches a plugin in real Obsidian, and resumes after a drop | pending | Needs the spike vault opened in Obsidian (see below) |

Suite: `uv run --locked pytest -q`, 1562 passed and 1 skipped (1522 before M1).

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
