# WP02 M1: remote capture and device contract

Status: M1 contract, 2026-10-12. This is the input to M2's capture writer and M3's
credential enforcement. It does not expose a remote listener or install a phone
client. [The WP02 plan](02-remote-chat-and-capture.md) supplies later acceptance
gates; [WP01 protocol v1](protocol-v1.md) remains frozen.

## Source and migration boundary

This contract was checked against these exact RemoteNotes revisions:

| Receiver | Pinned source | Existing behavior |
| --- | --- | --- |
| Obsidian plugin | [`04a2774`](https://github.com/Omgitskillah/RemoteNotes/blob/04a2774ba9c72cd2599d7147822d51bb26b2fae6/obsidian-plugin/lib/service.js), `feat/obsidian-background-capture` | Loopback port 27124; `POST /v1/captures`; one 64-hex bearer; `{"content":"…"}` only; `Inbox/<ULID>.md`; quoted `uid` frontmatter; SHA-256 revision of written Markdown. Calls Obsidian's create API, so Obsidian must be open. |
| Rust service | [`046fe9a`](https://github.com/Omgitskillah/RemoteNotes/blob/046fe9a8b6a0c59693f055f7bb35c9eb66ea85b7/vault-access/src/service.rs), `main` | Same route, request and response field names. Creates through its vault API with an unquoted `uid` and a BLAKE3 revision. |

Both return 201 with `ok`, `uid`, `path`, `revision`, authenticate before
reading the body, reject unknown request fields and accept at most 256 KiB of
encoded JSON. A replay of either old request **creates another note**. Their
revision algorithms and frontmatter bytes differ, so clients must treat
`revision` as opaque. The frozen [examples](../../tests/fixtures/wp02_capture_v1.json)
record both envelopes and the old field shapes. They are source-derived fixtures,
not claims of a captured response from a running RemoteNotes instance.

Nuru's WP01 service stays on `127.0.0.1:27125` for direct Obsidian use. M3 must
add a separate remote-only listener on `127.0.0.1:27126` and target **only that
listener** with the private Tailscale Serve HTTPS route. Serve forwards to
loopback, so the peer address cannot distinguish a proxied call from a direct
one. The listener that accepted the connection is the authority boundary: the
remote listener accepts only scoped device credentials or browser sessions and
never accepts the WP01 local token. It must not infer that boundary from
`Host`, `Origin` or `X-Forwarded-*` headers. Both listeners can share the service
and capture ledger, but the WP01 listener keeps its existing local-only policy.
M3 must inspect and verify Serve's target before cutover and during its private
HTTPS check; a route aimed at port 27125 fails the migration gate.

Migration is an operator action: inspect the current Serve route and receiver,
let accepted writes finish, stop the old receiver, switch the route to 27126,
verify one legacy capture and the Android setup route, then update shortcuts.
Never enable Funnel, overwrite an existing Serve route, stop port 27124's listener or rewrite
installed shortcuts automatically. Rollback restores the prior route/receiver;
the capture ledger, received notes and device records remain intact for a later
forward migration. M3 must test this procedure on a disposable vault.

## Device authority

Nuru remains one operator and one host-configured vault. Each credential has a
stable server-assigned device ID, a set of scopes, state and expiry. Rotation
changes the secret while retaining the device ID; revocation disables that ID's
access immediately. A client cannot choose or assert a vault, operator, device ID
or conversation owner. Server records supply those identities. The same operator's
authorized chat devices may share conversations; capture status is restricted to
the device that accepted that capture. Requests from another operator/vault are
foreign even if their IDs happen to match.

The executable [permission table](../../src/core/service/remote_contract.py)
and [failure matrix](../../tests/fixtures/wp02_capture_v1.json) define the M3
integration target:

| Scope | Allows | Does not imply |
| --- | --- | --- |
| `capture:create` | Direct Inbox capture and status for that device's request | Service vault status, chat, note preview/search or arbitrary writes |
| `chat:use` | Conversation create/list/resume, event stream, answer and Stop, plus sanitized service status | Reading note content or calling a note mutation |
| `notes:read` | Bounded note preview; with `chat:use`, model note reads/searches | Chat without `chat:use`, or writes |
| `notes:write` | With `chat:use`, reach Nuru's existing write policy and user approval | A direct general-purpose write API or bypassing approval |

The service must check scopes both at each route **and at tool dispatch**. A model
must not gain a note read/write merely because a user can submit a chat prompt.
Authenticate and check route scope before consuming a request body. For a capture,
read and parse the bounded body to obtain `request_id`, then check the
device-scoped request key, ownership, conflicts and tombstones before reading
vault data or loading a model. Resource IDs already in the URL can be checked
for ownership before a body is read. Unknown operations and inactive
credentials fail closed. The capture bearer grants only `capture:create`; the
Obsidian local token's existing chat access may be kept for direct loopback use,
but never promoted to a phone credential or accepted via the proxy by default.
New remote chat/read grants need explicit host confirmation. A chat write also
needs `notes:write` and the usual per-write approval.

To keep an installed shortcut working at the same URL after route migration, M3
must offer an explicit operator import of its old 64-hex token as a capture-only
device credential. The operator supplies the token; Nuru keeps only a hash and
does not silently read Obsidian plugin settings. If the token is not imported,
the operator must update that shortcut before switching the route.

The pinned Obsidian plugin also exposes an Android HTTP Shortcuts import via
`GET /v1/android-setup/{64-hex-token}.json`. Its unauthenticated setup token is
random, expires after five minutes, and the successful JSON download contains
the HTTPS `/v1/captures` URL and capture bearer. The plugin currently allows
repeated GETs while that link is live; its path disappears on stop or new pairing.
M3 must keep this route and HTTP Shortcuts schema-90 import shape for newly
generated Android setups, including one category/shortcut, `POST`, bearer auth,
`application/json`, `{{payload}}`, and the prepare/success scripts. Nuru may
issue its own capture-only device bearer in the download. Its setup token must
expire in five minutes or on restart, be consumed after one successful download,
and return 404 when unknown, expired or consumed. Keep the JSON download's
`Content-Disposition: attachment; filename="remote-notes.json"`,
`Cache-Control: no-store` and `Referrer-Policy: no-referrer`; a wrong method on a
live token returns 405.
An old setup link cannot survive migration because the old receiver owns its
temporary token. The operator must generate a fresh link after cutover; an
already installed Android shortcut keeps working through the imported bearer.
The fixture freezes the route, response headers and import fields for M3 tests.

The proposed credential lifecycle for M3 is: single-use pairing codes expire in
five minutes and on service restart; browser sessions use Secure, HttpOnly,
SameSite=Strict cookies and expire after 24 hours idle or seven days absolute;
new shortcut bearers expire after 90 days. Existing capture-only secrets can stay
valid until the operator rotates or revokes them, and never acquire broader
scopes. Logout revokes that session; rotating a device invalidates its old secret
and sessions, while preserving its capture request history. M3 must implement and
test these bounds before any remote access is enabled.

The phone page and API share one HTTPS origin. Browser mutations also require
Origin/Host checks and a session-bound CSRF value; neither a client-supplied
`X-Forwarded-*` header nor a raw bearer from the old local plugin confers proxy
identity. The remote listener rejects the local token even if a caller reaches
port 27126 directly. A capture-only bearer has no access to the phone page's
history or notes. Never put long-lived secrets in URLs, localStorage, logs or vault
files.

## Capture request and response

`POST /v1/captures` accepts exactly one of these UTF-8 JSON bodies:

```json
{"content":"# An idea\nNext line"}
{"request_id":"phone-20261012-0001","content":"# An idea\nNext line"}
```

The first is the legacy, single-shot form. It continues to return 201 with the
four existing receipt fields but cannot promise safe retry after an ambiguous
timeout. Old shortcuts must not be changed to retry automatically. New clients
persist a 16–128 character request ID before sending the second form and reuse it
on every retry, including after a phone reload. They must query status before
deciding what happened after a lost response. The schema is
[`capture-v1.schema.json`](../../src/core/service/capture-v1.schema.json);
[`remote_contract.parse_request`](../../src/core/service/remote_contract.py)
checks size, shape and blank text without logging the submitted body.

The decoded `content` string is preserved exactly after a documented frontmatter
prefix, including Unicode, quotes, whitespace and mixed newlines. Whitespace-only
content, duplicate JSON fields and unpaired Unicode surrogates are invalid.
These are deliberate strictness differences from the pinned Obsidian receiver:
its `JSON.parse` accepts duplicate `content` keys using the last value and lone
surrogates; Nuru rejects both. Its `TextDecoder` strips an initial UTF-8 BOM and
JavaScript `trim()` treats `U+FEFF` content as blank; Nuru rejects a leading JSON
BOM but accepts a `U+FEFF`-only content string because Python `strip()` does not
remove it. Conversely, Nuru rejects `U+001E`-only content as blank while the
plugin's `trim()` accepts it. M2 must preserve these choices in its HTTP parser;
an old shortcut sending any newly rejected edge case must be corrected.
Unknown fields such as `path`, `vault` or `properties` are invalid; the client
never chooses a filename or note metadata. The encoded JSON body limit is
256 KiB. M2 adds bounded upload time, queue and per-device quota.

On success Nuru creates `Inbox/<ULID>.md` with
`---\nuid: "<ULID>"\n---\n` plus exact content, matching the plugin's current
bytes. An existing Rust-created note is left untouched. A capture UID is the
client-visible note identity; regular Nuru notes may also have an internal `id`
and `type`, but captures are **not** silently converted to that schema. The
capture ledger binds the UID to the path, device, request and creation receipt.
Nuru's existing path-based read tools can open the note. The returned revision is
the actual backend revision of the bytes written, not a made-up acknowledgement;
clients compare it only as an opaque value. The immutable creation receipt keeps
its original revision even if an external editor later changes the note.

For a completed capture, return **201** and the original
`{"ok":true,"uid":"…","path":"Inbox/….md","revision":"…"}` body on every
identical retry. A changed decoded content under the same request ID returns
**409** and makes no new note. Different request IDs create different notes,
even with identical text. A duplicate still running returns **202** with its
request status URL, such as
`{"v":1,"request_id":"phone-20261012-0001","state":"running","status":"/v1/captures/requests/phone-20261012-0001"}`;
it never starts a second writer or a model call. No-ID legacy
requests remain single-shot and each accepted call makes a distinct note.

Capture errors retain the old `{"ok":false,"error":"code"}` envelope. The
legacy codes for malformed input, missing bearer, upload timeout, size, media
type and writer failure remain `invalid_capture` (400), `unauthorized` (401),
`body_timeout` (408), `body_rejected` (413), `expected_application_json` (415)
and `capture_failed` (500). The Obsidian receiver also uses `not_found` (404),
`method_not_allowed` (405) and `stopping` (503); M2/M3 must retain these route
and shutdown responses. New cases add `forbidden` (403),
`conflict` (409), `gone` (410), and a bounded-capacity error (M2 chooses its
status and retry advice). No error body contains note content or a host path.

`GET /v1/captures/requests/{request_id}` returns an authenticated, device-owned
status: `accepted`, `running`, `completed` with the original receipt, `uncertain`,
or `gone` for an expired full receipt whose ID remains reserved. Unknown IDs and
IDs owned by another device both return the same **404**
`{"ok":false,"error":"not_found"}` response. No one may use this endpoint to
enumerate another device's captures. An
uncertain state requires reconciliation; it is never silently retried. An ID with
a retained tombstone returns **410** to a replay, so expiry cannot create another
note. M2 must define and test the full-receipt retention and storage cap; when
the durable ledger is full it must reject new work rather than evict safety data.

The current WP01 `GET /v1/requests/{id}?operation=…` remains the chat-request
surface. Capture IDs use `device ID + vault ID + capture_create + request ID`,
not the shared operator principal, and decoded-content hashes omit JSON key order
and escape spelling. The new capture status route avoids exposing other kinds of
request records to a capture-only credential. M2 must persist the canonical hash,
reserved UID/path, intent, state and receipt in one recoverable ledger.

## Crash and authorization checks for M2/M3

| Boundary or threat | Required behavior |
| --- | --- |
| Lost response or 100 parallel retries | One request record, one allocated UID, one file and one logical receipt; status recovers it. |
| Crash before/after no-clobber create or receipt commit | Reserve UID and durable intent first; reconcile that exact path and expected byte hash after restart. Ambiguous evidence stays `uncertain`; never create a replacement UID. |
| Existing path, symlinked Inbox, external edit | Never overwrite or follow out of the vault; do not treat a changed note as proof of the original receipt. |
| Reused ID with a different decoded body | 409 before any vault operation, including when only a tombstone survives. |
| Capture-only credential calls chat/read/write routes | 403 before vault read, model dispatch or mutation. Unknown/revoked/expired credentials receive 401. |
| Browser CSRF, forged proxy headers or leaked setup URL | Origin, Host, CSRF and trusted-proxy checks; setup URLs single-use/short-lived and no-store. |
| Large/stalled body or quota flood | Bound bytes, time, queue and storage; keep status responsive; do not log submitted content. |

These are contracts and frozen fixtures, not M2/M3 implementation claims. The
model-free M1 tests cover the old payload and response shape, exact note envelopes,
the new schema and an explicit allowed/denied capability matrix. M2 then proves
the capture state machine against a real scratch vault and process crashes; M3
proves the route decisions through HTTP and private HTTPS.
