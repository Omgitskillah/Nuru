# nuru-serve protocol, version 1

Status: frozen for WP01 M1, 2026-10-09. A change that breaks
`nuru/tests/fixtures/protocol_v1.json` is a protocol change and needs version 2.
[Version 2](protocol-v2.md) (WP04) changes only the status's vault identity;
a v2 service serves all of version 1, unchanged.

Shapes: [`protocol-v1.schema.json`](../../src/core/service/protocol-v1.schema.json).
Rules a schema can't state: [`protocol.py`](../../src/core/service/protocol.py) (`StreamCheck`,
`Display`, request hashing). Tests: `nuru/tests/test_service_protocol.py`.

## Transport

- HTTP/1.1 on `127.0.0.1`, port 27125 by default (RemoteNotes uses 27124), configurable.
- JSON request and response bodies, at most 256 KiB per request.
- A turn's events come as server-sent events (`text/event-stream`). Each frame has
  `id: <seq>`, `event: <type>` and one line of `data: <event JSON>`. `: keepalive` comment
  frames keep idle connections open. Clients read the stream with `fetch` and a body reader,
  not `EventSource`, because `EventSource` can't send an `Authorization` header.
- The version is in the path (`/v1/...`) and in every body (`"v": 1`). An unknown version
  gets `unsupported_version`.

## Who may call

| Check | Rule | Refusal |
| --- | --- | --- |
| Host | `127.0.0.1:<port>` or `localhost:<port>` only, against DNS rebinding | 403 `forbidden` |
| Origin | No Origin header (a native client), or one on the allow-list: `app://obsidian.md` in WP01 | 403 `forbidden_origin` |
| Credential | `Authorization: Bearer <token>`, required on loopback too. Compared in constant time | 401 `unauthorized` |
| Ownership | A conversation, turn, question or request belongs to the principal that created it. Another principal gets `not_found`, not `forbidden`, so IDs don't leak whether they exist | 404 `not_found` |

The service makes no tool or model call before all four checks pass.

- **Principal.** Each credential names a principal and a client. WP01 has one principal,
  the operator, with a credential per client. Every client of the operator sees the
  operator's conversations, and can answer the operator's questions.
- **Credentials.** Service secrets live outside the vault, under `~/.nuru/service/`
  (directory 0700, files 0600). The service stores only a hash of each client token.
- **Pairing.** The plugin keeps its token in an OS-local file outside the vault. Synced
  plugin data, settings and URLs never hold it.
- **Vault identity.** Status gives the vault's `id`, a hash of its resolved path, and never
  the path itself: the first 32 hex digits of the SHA-256 of the absolute path with symlinks
  resolved, as UTF-8 (`protocol.vault_id`). The plugin computes the same hash for its own vault, and refuses to
  pair or chat if the two differ.
- **Paths.** Paths in requests are vault-relative (see `vault_path` in the schema). The
  service resolves them against its own vault and refuses any that leave it, including
  through a symlink. No request can choose the vault root.

## Operations

| Operation | Body | Answer |
| --- | --- | --- |
| `GET /v1/status` | none | `status`. It works before any model is loaded. `models`, when present, lists what a conversation may run on (see [Models](#models)). |
| `POST /v1/conversations` | `create_conversation` | `{v, conversation_id}` |
| `GET /v1/conversations?before=&limit=` | none | `{v, conversations: [{id, title, created_at}]}`, newest first. `limit` is at most 50 (default 20); `before` is the last ID of the previous page. |
| `GET /v1/conversations/{id}?before=&limit=` | none | `{v, conversation_id, title, created_at, model, thinking, turns: [{turn_id, text, state, display}]}`, newest first, at most 50 turns per page. `display` is what `Display` shows for the turn. |
| `POST /v1/conversations/{id}/turns` | `submit_turn` | 202 `accepted_turn`, sent once acceptance is stored and before generation starts |
| `GET /v1/turns/{id}/events?after={seq}` | none | Replays the events after `seq`, then follows live ones until the terminal event |
| `POST /v1/turns/{id}/cancel` | `cancel_turn` | 202 `{v, conversation_id, turn_id}`, also for a turn that has already ended |
| `POST /v1/questions/{id}/answers` | `answer_question` | 200 `{v, ...}` with the `question_closed` data, or 409 `question_closed` / `wrong_operation` |
| `GET /v1/requests/{request_id}?operation=` | none | `request_state`, for a client that lost the response |
| `POST /v1/service/stop` | `{stop_key}` | Only on the local listener, and only for a service started with `--managed` (by the Obsidian plugin), which writes the key to `~/.nuru/service/managed.json`, owner-only. 202 `{stopping: true}`, after which the service shuts down as for Ctrl-C; 403 `forbidden` for a wrong key; 409 `conflict` while a turn is running or waiting; 404 for any other service. This is how a plugin in another vault switches the service to its own. |

### Request IDs

- Every POST carries a client-chosen `request_id`. It is scoped to the principal, vault
  and operation (`request_key`).
- It is bound by `payload_hash` to the rest of the body and to the resource the URL names:
  - the conversation a turn goes to;
  - the turn to cancel;
  - the question to answer;
  - `""` for a new conversation.
- The service stores the request before it acts on it.
- A retry with the same ID and body gets the original result, with `replayed: true`. It
  never starts a second turn.
- The same ID with a different body, or aimed at a different resource, gets 409 `conflict`
  (the `request_binding` fixtures).
- Compact tombstones keep old IDs for as long as the database lives in this release, so
  an old ID never becomes new work.

## A turn's events

Every event has these fields:

- `v`
- `conversation_id`
- `turn_id`
- `seq`: 1, 2, 3… with no gaps
- `at`: UTC, display only
- `type`
- `data`: the shape depends on `type`

| Type | Meaning |
| --- | --- |
| `accepted` | Always first. Its `request_id` is the one the turn came from. |
| `queued` | Waiting for the one generation slot. `position` counts from 1. |
| `started` | Generation has begun. Content events come only after it. |
| `text` | A piece of the reply, at most 16,000 characters. A reply that Session buffers is split to fit. Chunks may split words, Markdown or Unicode clusters, but never a code point. |
| `reasoning` | Optional display data. Chunks have `final: false`. `final: true` carries the whole call's reasoning and replaces the chunks before it. No turn needs these to finish. |
| `retry` | The model call is being run again (`silent`: it only reasoned; otherwise why it was steered). |
| `tool_start` / `tool_result` | A tool call (`call_id`, name, arguments), then whether it worked (`summary`: the error code when it didn't). A call can be left without a result: an enforced write that is started again with corrected arguments leaves the first start open. |
| `question` | A question Nuru needs answered before it can go on. Details under Questions. |
| `question_closed` | `answered`, `expired` or `cancelled`. Only `answered` has `chosen` (possibly empty) and, where `allow_other` is set, `other`. `decision` is what Nuru acts on. Details under Questions. |
| `write_receipt` | What a write actually did, after its `tool_result`: `applied`; `refused` (not run on purpose: a policy, a No, a blocked write); `failed`; or `uncertain` (it may have run; never retried). An `applied` receipt's `path` is the file the write actually changed, as the notes server reports it (a title or a daily resolves to its file; an archive or trash move names where the note went); it is left out when that can't be told, and on other states. Clients show this, not the request. |
| `completed` | The reply, plus `ask` if the model asked the user something (the next message answers it). A reply over 60,000 characters keeps its start and adds `truncated: true`; the conversation history keeps the whole reply. |
| `failed` | `turn_failed`, `model_unavailable`, `service_restarted` or `internal`, with a message fit to show the user. |
| `cancelled` | `user` (Stop) or `shutdown`, with the reply so far (truncated the same way). |

A stream has these rules (`StreamCheck` enforces them):

- It has exactly one terminal event (`completed`, `failed` or `cancelled`), and it is the
  last event. The terminal types exclude each other.
- A turn can be cancelled or fail while queued, before `started`.
- Every question is closed before the terminal event. Stop closes an open question as
  `cancelled`.
- `tool_result` and `write_receipt` name a `call_id` that `tool_start` has already
  opened.
- One event is at most 256 KiB. The limit counts the whole SSE frame as sent: compact JSON
  plus the `id`/`event`/`data` lines. Text fields are bounded so they always fit, and
  longer tool arguments are cut for display: each value to 2,000 characters (as JSON when it
  isn't a string), ending `…(cut: N characters)`, and at most 32 of them.

### Reconnecting

- A client remembers the highest `seq` it has applied. It reconnects with `?after=<seq>`,
  and drops any event at or below that number. `Display` shows this rule.
- A replay from any cursor, or the same event delivered twice, therefore shows nothing
  twice.
- A cursor beyond the turn, or into a turn whose events were compacted, gets 410
  `cursor_expired`, with `resync` saying where to rebuild from. It never starts a new turn.
- Retention: a turn's events are kept for 30 days after its terminal event. After that
  the turn keeps only its final display state: the reply, receipts and decisions.

## Questions

- A mid-turn question has a `question_id`, an `expires_at` and an `operation`.
  `operation` is `operation_hash(turn_id, question_id, question)`, so it ties the
  question to the one tool call it is about.
- The question expires on the host, 10 seconds after it is asked (`questions.ANSWER_SECONDS`).
  The host's clock decides, not the client's.
- The service accepts an answer only if all of these hold:
  - it comes from the turn's principal;
  - it arrives before `expires_at`;
  - it repeats the question's `operation`;
  - it picks labels the question offers, each once:
    - `yes_no` and `single` take exactly one label, or, when `allow_other` is set, the
      user's own words in `other` instead;
    - `multi` takes any of its labels, including none, plus `other` when it is allowed.
- An answer that misses any of these is refused:
  - a late, duplicate or second answer gets `question_closed`;
  - an answer for another operation gets `wrong_operation`.
- A refused answer never changes the decision.
- The `decision` follows from the answer:
  - no answer is No;
  - an answer that picked nothing is "None of these.";
  - otherwise it is the chosen labels and then `other`, joined with "; " (`questions.answer_text`).
- `StreamCheck` checks every `question_closed` against the question it closes.
- Closing the panel, disconnecting or stopping never counts as Yes. When a question
  closes without an answer, `question_closed` records `expired` or `cancelled` with
  decision No.

## Cancelling

- `cancel` is idempotent. It stops generation at the next safe point.
- A turn waiting on a question closes it as `cancelled`.
- A turn still in the queue is cancelled before it starts.
- Tools that already ran stay done: their `write_receipt` events come before the
  `cancelled` event.
- Cancelling a finished turn returns 202 and changes nothing.

## Writes the service can't vouch for

A crash between a write and its receipt leaves the write `uncertain`. It is recorded
(`write_attempts`, as today), reported with `write_receipt` `state: uncertain`, and never
replayed on its own. The operator resolves it.

When the service starts again, each write call of an unfinished turn that has no result yet gets
one from its own entry in the write ledger, found by the call's `call_id`: `completed` is
`applied` (with the file it changed), `not_completed` is `failed`, and `in_flight` or `uncertain`
is `uncertain`. A call with no entry never ran and gets nothing. These come before the turn's
`failed` (`service_restarted`).

## Attachments

A turn's `attachments` are only what the user attached. The model is told each note's path
and reads the note itself, with its own tools and under the same rules as any other read; a
`selection` is quoted in the message. The service never pastes a note's contents in. A path
that resolves outside the vault, through a symlink too, is refused with `invalid_request`.

## Models

`status.models` is `{default, choices}`: the configured model's id (`NURU_BASE_MODEL`, or
`anthropic:<model>` from `NURU_MAIN_MODEL`) and each model a conversation may pick, the default
first. Each choice is `{id, label, cloud, thinking, default_thinking}`: `cloud` is true for Claude,
where what the model reads is sent to Anthropic; `thinking` lists the levels that differ for that
model (`[{id, label}]`, empty if it can't think). Local choices are the supported models pulled in
Ollama; Claude's appear only when an API key is set.

`submit_turn` may carry `model` and `thinking`. They're stored on the conversation, and this turn
and the next ones run with them; a turn without them runs on what the conversation last ran on. A
new `model` resets `thinking` to that model's default unless the same message gives one. A model
the service doesn't list, or a level the model doesn't have, is `invalid_request`. A conversation's
`model` and `thinking` read back as `null` for the defaults. The whole conversation's history goes to
whichever model runs the turn. Switching from one local model to another unloads the first.

## Errors

Error bodies have the form `{v, error: {code, message, retry_after_seconds?, resync?}}`.

| Code | HTTP |
| --- | --- |
| `invalid_request` | 400 |
| `unsupported_version` | 400 |
| `unauthorized` | 401 |
| `forbidden_origin` | 403 |
| `forbidden` | 403 |
| `not_found` | 404 |
| `conflict` | 409 |
| `question_closed` | 409 |
| `wrong_operation` | 409 |
| `cursor_expired` | 410 |
| `payload_too_large` | 413 |
| `queue_full` | 429 |
| `internal` | 500 |
| `model_unavailable` | 503 |
| `shutting_down` | 503 |

- A refusal always carries the CORS headers for an allowed origin, so the plugin can tell
  a wrong token from a missing service.
- Error messages never include credentials, note contents or anything the client sent.
  They name the field and the rule it broke, such as `text: maxLength is 32000`.

## Not in version 1

- Several vaults.
- Accounts beyond the one operator.
- Remote access, which is WP02. It reuses this contract over its own authorization.
- Windows.
- Model-assisted maintenance.
