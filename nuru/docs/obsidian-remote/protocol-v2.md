# nuru-serve protocol, version 2

Status: WP04 M1, 2026-10-18. Version 2 is **only what changed** from [version 1](protocol-v1.md): the
vault is named by a UUID instead of a hash of its path. Everything else is version 1, served at `/v1/…`
exactly as before by a service that also speaks version 2. A v1 client never sees a UUID or a v2 error.

Shapes: [`protocol-v2.schema.json`](../../src/core/service/protocol-v2.schema.json), which defines only
`status` and `error`. Tests: `nuru/tests/test_vault_identity.py`.

## The vault's identity

A vault's ID is the UUID in `<vault>/.nuru/vault-id` (`core.vault_identity`). `nuru-serve` writes it
the first time it serves the vault. It survives the vault being moved or renamed, unlike v1's path hash.
A client in the vault (the Obsidian plugin) reads the same file. A vault without the file has never
been served and matches no service.

Each computer records where it last saw each UUID (`~/.nuru/vaults/registry.json`). A vault whose UUID
belongs to another copy that still exists isn't served until the user settles it at the terminal:
- `uv run nuru-serve vault rebind <vault>` keeps this copy as the vault, with its history.
- `uv run nuru-serve vault clone <vault>` makes it a separate vault, with a new ID and no history.

`uv run nuru-serve vault list` shows the registry.

## `GET /v2/status`

v1's `status`, with:
- `v`: `2`.
- `vault`: `{uuid, name}`, in place of v1's `{id, name}`. On the remote listener, `vault` is left out,
  as in v1.

## Errors

v1's error body with `v: 2`, and one more code:

| Code | HTTP | When |
| --- | --- | --- |
| `wrong_vault` | 409 | A request meant for one vault reached a service serving another (WP04 M4). `served: {uuid, name}` says which vault is served. |

## Versions together

| Client | Service | What happens |
| --- | --- | --- |
| v1 (older plugin) | v1 + v2 | Chats over `/v1/` as before; `/v1/status` still gives the path hash. |
| v2 (this plugin) | v1 only (older Nuru) | `/v2/status` answers 400 `unsupported_version`; the plugin says "This plugin needs a newer Nuru" and sends nothing. It never falls back to the path hash. |
| v2 | v1 + v2 | `/v2/status` for the vault check; everything else at `/v1/`. |

Any other `/v2/…` path, and any other version, answers 400 `unsupported_version`.
