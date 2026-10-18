# WP04 — Each vault's data kept apart, on the computer

Status: proposed, 2026-10-18. The user has chosen where the data lives. This plan covers how to get there.
Discussed on the board, in "Keeping each vault's Nuru data with the vault" (Claude and Astra).

Depends on the Obsidian auto-start (PR #29). Comes before running several vaults at once (TODO R26), which
it makes possible.

## Outcome

Each vault's Nuru data is kept apart from every other vault's, and none of it is in the vault:

| What | Where | Why |
| --- | --- | --- |
| The vault's identity | `<vault>/.nuru/vault-id`: a random UUID, one line | Survives moving the vault; small and safe to sync |
| Conversations, the service's state, turn logs, indexes | `~/.nuru/vaults/<uuid>/` | Live SQLite must not sit in a synced folder; history must not travel with the vault |
| Tokens, paired devices, `install.json`, `managed.json` | `~/.nuru/service/`, `~/.nuru/` | Secrets and machine state are per computer |
| The notes server's write lock | `<vault>/.obsidian-mcp-write.lock` (unchanged) | It coordinates writers to the vault itself |

A conversation can't be listed in another vault, or continued there. Moving a vault keeps its history.
Deleting a vault leaves its data on the computer until `nuru-serve vault forget` removes it.

## Today

- **The vault ID** is the first 32 hex digits of the SHA-256 of the vault's resolved path (`protocol.vault_id`,
  and `vaultId()` in the plugin). Moving or renaming the vault changes it.
- **Conversations and service state** are in `nuru/.db/conversation.db` and `service.db`, in the Nuru
  folder. Neither is keyed by vault: the `conversations` table has no vault column.
- **Turn logs** are in `nuru/.logs/`.
- **The notes server's note index and semantic index** are in the vault (`.obsidian-mcp-index.sqlite3`,
  `.obsidian-mcp-semantic-v2.sqlite3`). The semantic one can already be placed elsewhere with
  `OBSIDIAN_SEMANTIC_DB`. The note index can't yet.
- **The vault ID is used** in request keys (`protocol.request_key`), capture records (`captures.vault_id`),
  the remote contract, the status reply and the plugin's same-vault check.

## Milestones

### M1 — The vault's identity

- `core.vault_identity` (Python) and the plugin both read `<vault>/.nuru/vault-id`. The service creates
  the file the first time it serves a vault without one. It's 36 characters, UUID4.
- A registry, `~/.nuru/vaults/registry.json`, records each UUID's last-seen path.
- **A copied vault:** if the UUID is already registered at another path that still exists and still holds
  the same `vault-id`, the service refuses to serve it. It asks for `nuru-serve vault rebind <vault>`
  (it's the same vault, moved, and the old copy is stale) or `nuru-serve vault clone <vault>` (give it
  a new UUID and an empty history). If the old path is gone, it's a plain move: the registry is updated
  and nothing is asked.
- `status.vault.id` becomes the UUID. The plugin compares it with its own vault's `vault-id`.
  - **Done when:** a moved vault keeps its ID and its history; a copied one is refused until rebound or
    cloned. The plugin and the service always agree on the ID.

### M2 — Data under `~/.nuru/vaults/<uuid>/`

- `conversation.db`, `service.db`, `logs/`, `semantic.sqlite3` and `note-index.sqlite3` live there.
  `NURU_DB_PATH`, `NURU_LOG_DIR` and `OBSIDIAN_SEMANTIC_DB` still override, for tests and evals.
- **The notes server:** a change in `servers/obsidian` lets the note index be placed elsewhere, as the
  semantic one can.
- `ServiceStore` keeps its single-owner check, now per vault folder.
- **Done when:** no SQLite file is created in the vault, which a test checks after a turn, a capture and
  an index pass. Two vaults served one after the other never see each other's conversations.

### M3 — Moving today's data

- **Indexes:** they can be rebuilt, so they aren't moved. The new ones are built on the first pass. The
  old in-vault files are left for the user to delete, and `nuru-serve vault` lists them.
- **Conversations, service state and logs:** moved to the vault the service is serving at the first start
  under the new layout. The old files are kept as `*.pre-wp04` until the user removes them. *(Question
  for the user, below.)*
- **Request keys and capture records** that include the old path-derived ID are rewritten to the UUID in
  the same transaction. A replay of a request made before the move still gets its stored answer.
- **Done when:** after the move, every existing conversation opens with its turns and events. A capture
  replayed from before the move is still recognised, and nothing is written twice.

### M4 — Phones bound to one vault

- **Pairing** records the vault's UUID in the device's credential: one vault per computer for a phone.
- **When that vault isn't being served,** a capture or chat from the device is refused with 409
  `wrong_vault` and a retry message, such as "Nuru is serving another vault on this Mac. Try again when
  it's back on <vault>." Nothing is queued: an offline queue would be its own decision.
- `device list` shows each device's vault.
- **Done when:** with the service switched to another vault, a capture is refused and lands nowhere.
  Once switched back, the same request is accepted once.

### M5 — Acceptance

These checks run on a real Mac:
- move a vault;
- copy a vault and refuse it;
- switch between two vaults from Obsidian;
- confirm the iCloud-synced vault holds no Nuru SQLite;
- send a phone capture while the other vault is served;
- run `vault forget`.

## Questions

1. **For the user:** today's conversations aren't tagged with a vault. Should they all move to the vault
   being served at the first start, which is NuruVault today? Or should they go to a read-only archive
   that no vault lists?
2. **For Astra:** changing `status.vault.id` from a path hash to a UUID changes what a v1 client sees.
   The plugin is the only client that checks it, and the remote listener drops `vault`. Is a note in
   the protocol enough, or does it need a version bump?
3. **For Astra:** should `vault rebind` and `vault clone` also be offered in the Obsidian panel, or stay
   at the terminal, since they decide which copy owns the history?
