# WP02-M3: device pairing and private HTTPS

Implementation: 2026-10-13. The model-free HTTP suite is the automated gate.
**Real Tailscale HTTPS cutover and rollback are still pending**; this milestone
must remain in progress until their evidence is recorded. Phone chat and
request-aware shortcut templates remain M4.

## Start without changing existing routes

Use the same vault and service/conversation database as the local service. There
is one host, worker and capture ledger, with two independent accepting sockets:

```sh
uv run nuru-serve --remote-origin https://YOUR-MAC.YOUR-TAILNET.ts.net
```

Local Obsidian remains at `127.0.0.1:27125`. The opt-in remote listener binds
`127.0.0.1:27127`. Port 27126 remains for manual test servers. Neither listener
accepts the other's credential class. Forwarding headers never change that
boundary. The configured HTTPS Host must match exactly; no other browser Origin
is accepted. Starting the service does not configure Serve, Funnel, shortcuts,
login items or the old receiver.

Each listener admits at most 32 connections, with a backlog of 32. Excess
connections close immediately. Headers have a five-second total deadline and
bodies have a five-second upload deadline. Captures retain their 256 KiB limit,
per-device quota and durable storage limits. Chat uses the existing bounded queue. Each device is limited to 120 authenticated
API requests per minute across both credential rotation and its allowed routes;
429 includes Retry-After. Capture creation also has its stricter M2 quota.
Every authentication and route-scope decision precedes body reads; URL resource
ownership precedes mutation body reads. Credentials are rechecked after uploads,
while streaming events, before queued inference starts, and at model/harness tool
dispatch. An already dispatched operation may finish after revocation; revocation
does not undo an accepted durable capture or an in-flight write.

## Approve devices on the Mac

These commands contact the authenticated **local** listener, using the local
`obsidian` token file. Use `--local-client NAME` for another local operator client.
A local token is full operator authority: protect its file as you protect the
vault. There is no remotely accessible device-management endpoint.

```sh
uv run nuru-serve device pair iphone --scopes chat:use notes:read capture:create
uv run nuru-serve device pair iphone --scopes chat:use notes:read notes:write capture:create
uv run nuru-serve device shortcut siri
uv run nuru-serve device android android-phone
uv run nuru-serve device list
uv run nuru-serve device cancel android-phone
uv run nuru-serve device revoke iphone
```

Running `device pair` is explicit host confirmation of exactly the named scopes.
The command returns a single-use code. Open the configured HTTPS origin on the
phone and enter it there. Codes expire in five minutes, on cancellation, on
rotation/revocation, or on service restart. An unredeemed code grants no API access.
The minimal pairing page displays permissions and provides sign-out; it is not
the M4 chat client. Never send codes or credentials to logs or commit them.

Browser sessions use `__Host-nuru`, Secure, HttpOnly, SameSite=Strict, Path=/.
They expire after 24 hours idle or seven days absolute. Cookies survive service
restart subject to those limits. `GET /v1/session` supplies the session-bound CSRF
value to same-origin JavaScript; every authenticated browser mutation requires
both the exact Origin and `X-Nuru-CSRF`. Pairing instead requires its one-time code
in `Authorization: Pairing CODE` and the exact Origin. Logout revokes the session
on the server and clears its cookie. No token is placed in localStorage or a URL.

Shortcut bearers have only `capture:create` and expire after 90 days. Reissue the
same device name to rotate; the old secret/session and any onboarding links stop
working, while its device ID and capture receipts stay intact. Revoke removes the
device grant; recreating that name gets a new ID, without the old capture history.
Device records are bounded at 128. Only secret hashes persist in
`~/.nuru/service/devices.db` (0600, directory 0700), outside the vault; the
`NURU_SERVICE_DIR` override applies to both local and remote credentials.

`chat:use` permits shared operator conversation history, so it can disclose prior
chat text and prior note excerpts. `notes:read` additionally permits new vault
reads/searches through the model. `notes:write` requires chat/read and reaches the
existing write-policy and approval gates; it never bypasses them. Answering a
live write approval also requires `notes:write`. All model and harness tool calls
are checked, including policy lookups and Python's note-input reads. Unknown tools
fail closed; the internal decision tools and pure computation tools are explicitly
allowlisted. A standalone note-preview endpoint is part of the M4 phone UI work.

The Android setup command rotates a capture-only bearer and returns a private
five-minute download URL. `GET /v1/android-setup/{64-hex-token}.json` consumes it
once; unknown/expired/consumed tokens return 404, a wrong method on a live token
returns 405. Cancel, rotation, revocation and restart invalidate pending downloads.
Responses are no-store, no-referrer JSON attachments named `remote-notes.json`.
The schema-90 import retains the pinned RemoteNotes prepare/success scripts and
legacy single-shot behavior. Its URL contains a temporary setup secret, never the
bearer itself. Do not publish or log that URL. Closing the operator onboarding
workflow means running `device cancel NAME`. Installed legacy shortcuts must not
automatically retry an ambiguous result; request-aware templates arrive in M4.

## Inspect, migrate, and roll back

This is an explicit operator procedure. Do not automate overwriting an existing
Serve route, enabling Funnel, stopping port 27124, or rewriting installed shortcuts.
First rehearse with a disposable vault and preserve the current Serve JSON privately.
Use the Tailscale CLI installed on the host (`tailscale`, or the macOS app binary).

1. Inspect `tailscale status --json` and `tailscale serve status --json`. Record
   the current HTTPS origin, target, any additional handlers and whether Funnel
   is enabled. Identify the old receiver and let every accepted write finish.
   Keep its configuration for rollback. Do not assume Obsidian is the receiver.
2. Start Nuru with the remote origin, a scratch vault and isolated databases for
   rehearsal. Import an existing shortcut secret explicitly if its URL/token is
   to stay unchanged: `uv run nuru-serve device shortcut siri --import-legacy`.
   The command prompts privately, accepts the old 64-hex capture token, and keeps
   only its hash with capture-only scope. It does not read plugin settings.
   The same secret cannot belong to two named devices. Imports have no automatic
   expiry; rotate or revoke them explicitly.
3. Check installed clients' upload framing. Nuru requires Content-Length and
   rejects chunked uploads. Verify the actual shortcut before cutover; fix an
   incompatible client first. A body containing only `content` stays single-shot.
4. With operator approval, stop the old receiver after its writes drain. Switch
   the saved HTTPS root route to `http://127.0.0.1:27127` using the installed CLI's
   `tailscale serve --help`. Preserve unrelated routes. Never point it at 27125
   and never enable Funnel. Nuru supplies no automatic route-mutating command.
5. Run the read-only check:
   `uv run nuru-serve --remote-origin https://YOUR-MAC.YOUR-TAILNET.ts.net remote-check`.
   It requires a running matching node, HTTPS on 443, the exact remote target,
   no enabled Funnel, and no foreground route overrides. Also inspect the full
   Serve configuration yourself. This check alone is not an HTTPS transaction.
6. Over actual private HTTPS, verify missing, invalid, expired and revoked tokens
   reject before any note effects; pair a browser; test wrong Origin and CSRF;
   submit a legacy capture and a request-aware capture, compare exact bytes, and
   replay the latter for the original receipt. Generate a new Android setup link,
   download once, and verify a second download fails. Old setup URLs cannot
   survive because they belonged to the old receiver. Check status after rotation.
7. Rehearse rollback: drain Nuru requests, restore the saved route and old receiver,
   then verify an old shortcut. Keep all notes, device records and the Nuru
   service/conversation databases, including capture request tombstones. Do not
   delete the ledger to roll back. Request-aware clients must pause during rollback:
   the old receiver cannot deduplicate their retry or answer Nuru status requests.
8. Switch forward again and retry the same request-aware ID: require the same
   receipt and no new note. Record CLI/source versions, exact commands, note hashes,
   failures and cleanup, with no live secrets or personal notes in the evidence.

The automated rehearsal uses a disposable HTTP proxy and a frozen legacy responder
for steps 4/7/8. It proves retention through target changes, not real Tailscale,
Obsidian receiver shutdown, installed shortcut framing, or physical phone behavior.
On 2026-10-13, the actual host's HTTPS root still targets port 27124. It was inspected
but not changed. Those real migration/HTTPS checks remain an explicit open gate.
