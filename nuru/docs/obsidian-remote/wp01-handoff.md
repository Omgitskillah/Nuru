# WP01 handoff: compatibility, known limits and what WP02 can rely on

Status: written for WP01 M5, 2026-10-11. The evidence behind each claim is in
[`nuru/evals/obsidian_remote/wp01-local-chat.md`](../../evals/obsidian_remote/wp01-local-chat.md), where every
claim is marked proposed, automated-tested, manually-tested or live-model-tested. This page only points there.

## Protocol compatibility matrix

Protocol v1 is frozen ([protocol-v1.md](protocol-v1.md)). A change that breaks
`nuru/tests/fixtures/protocol_v1.json` needs v2.

| Client or component | Version checked | Protocol | Status |
| --- | --- | --- | --- |
| `nuru-serve` (this repo) | the M5 commit | v1 only; another `v` gets `unsupported_version` | automated-tested |
| Obsidian plugin `nuru` | 0.1.0 (`minAppVersion` 1.10.0, desktop only) | v1 | automated-tested (jsdom stand-in), manually-tested in real Obsidian |
| Obsidian desktop, macOS | app 1.13.7 (installer 1.10.6), macOS 26.6.2 | v1, Origin `app://obsidian.md` | manually-tested |
| Obsidian desktop, Windows or Linux | none | n/a | not tested; not a WP01 requirement (TODO R20) |
| Obsidian mobile (Android, iOS) | none | n/a | out of scope: the plugin is desktop only |
| Native clients without an Origin (curl, Python, Node) | Python 3.14.7, Node 24.21.0 | v1 | automated-tested |
| Browsers on any other origin | n/a | refused: 403 `forbidden_origin` | automated-tested; WP02 must add its own origin |
| CLI and Qt window | the M5 commit | do not use the service; call Session directly | automated-tested (unchanged); the same turn gives the same result through both (`test_service_equivalence.py`) |

Toolchain the checks ran with: Python 3.14 (`uv.lock`), Node ≥ 24.15 (24.21.0), npm 11.19.0, esbuild 0.28.2,
jsdom 30.1.2, markdown-it 15.0.2 (`plugins/obsidian/package-lock.json`).

## Known limits

Service and protocol:
- One vault and one operator per service. Every client of the operator sees all of the operator's conversations.
- One generation at a time across the service, with 4 turns waiting at most (`--queue`). More get 429.
- A mid-turn question waits 10 s (`questions.ANSWER_SECONDS`) and then counts as No. That suits a person at the
  desk. It is short for a phone.
- A turn's events are kept 30 days. After that, only its final display is left, and **Show all steps** says the
  steps are gone.
- Stop is a request. It doesn't undo a tool that already ran. A write whose outcome is unknown stays `uncertain`,
  blocks further writes, and is never retried automatically.
- Tool arguments are cut for display at 2,000 characters per value and 32 values per call.
- Attachments name a note. The model reads it with its tools; nothing is pasted into the prompt.
- Shutting down the service doesn't stop Ollama or unload a model it didn't start.
- The semantic index for a served vault: `OBSIDIAN_SEMANTIC_DB` from the environment if it is set. Otherwise
  `.env.obsidian`'s index, but only for `.env.obsidian`'s own vault. Any other vault gets
  `<vault>/.obsidian-mcp-semantic.sqlite3`. Before M5, a vault other than `.env.obsidian`'s silently used that
  vault's index. This rule is new in M5 and up for review in its PR.

Plugin:
- It reads its token only from `~/.nuru/service/clients/obsidian.token`. A second service on another port needs
  that file swapped (see the plugin README's scripted-server steps).
- Replies render through markdown-it with HTML and images off. Raw HTML shows as text, and images and embeds
  show as links. This is deliberate. It means a reply can't show a picture.
- A streaming reply is re-rendered at most every 80 ms (`RENDER_MS`). That is most of the 100 ms budget for
  getting a chunk on screen: the benchmark measures p95 ≈ 88 ms through ChatView. Any slower rendering, or a
  longer throttle, breaks the gate.
- The automated tests and the benchmark's plugin route run on a jsdom stand-in for Obsidian. Only the manual
  runs exercise Obsidian's own layout and paint.
- One unexplained event from the M3b manual run: the first message after the fetch fix showed nothing at first,
  although the service completed it. It didn't happen again.

The model (not the service, but seen through it):
- False "done" claims and doubled appends. In the WP01-ACT live turn, a second `write_note` appended the plan
  again, and the reply said boxes were checked when they weren't. Tracked as F8.
- When the model writes text both before and after a tool call, the reply glues the pieces without a space and
  sometimes repeats the answer. This happened in about 1 turn in 5 to 1 in 8 in the live lane, through Session directly and
  through the service alike (F9).
- The live-model lane passed 30/30 direct and 29/30 through the plugin (the miss is a rubric gap) on six frozen cases. That is six cases on one small vault, not a
  measure of accuracy in general. The service passes on whatever the model does; it doesn't make the model more accurate.

## What WP02 can rely on

Proven in WP01, and safe to build on:
- The v1 contract: request IDs scoped to principal, vault and operation, and bound to their content. Replay from
  any cursor without duplicates. 410 `resync` for expired cursors. Exclusive terminal states. Receipts matched by
  call ID.
- The host lifecycle: lazy model loading, status without a model, crash recovery that never reruns a turn,
  uncertain writes that stay blocked, one owner per database, clean shutdown.
- The plugin's protocol code (`lib/protocol.js`, `lib/turns.js`, `lib/client.js`, `lib/activity.js`). It has no
  DOM and doesn't depend on Obsidian, so a phone page can reuse it. Python's `Display` and the plugin's must
  stay equal (an e2e test checks every fixture stream).
- `NuruPluginTestVault` (`nuru/tests/plugin_test_vault.py`) and `scripted_serve.py`, for fixtures and client
  end-to-end tests without a model.
- The benchmark (`bench_wp01.py`) as the local baseline for WP02's local-versus-remote comparison.

Not proven, and WP02's to do:
- Anything remote. Loopback with one bearer token is not remote authorization. WP02 needs device credentials with
  capabilities, a same-origin phone UI (a new allowed origin), and checks behind Tailscale Serve.
- Questions answered from a phone: the 10 s wait, and what "nobody answered" means away from the desk.
- Captures (`/v1/captures`), which WP01 doesn't have.
- Running as a launch agent across login and reboot (WP02 M5).
- More than one operator or vault.
