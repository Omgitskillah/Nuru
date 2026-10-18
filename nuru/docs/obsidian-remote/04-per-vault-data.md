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
- **Protocol v2 carries the UUID.** v1 stays frozen as `protocol-v1.md` defines it, so nothing a v1
  client relies on changes meaning:
  - **`/v1/…` is still served, unchanged.** `status.vault.id` stays the path hash, and no new error codes
    are added. An old plugin keeps working against a new service, including its own same-vault check.
  - **`/v2/status`** gives `vault: {uuid, name}` and drops the path hash. It adds an error `wrong_vault`,
    409, carrying `{served: {uuid, name}}`, which M4 uses. `protocol-v2.md` and its schema are written
    in this milestone. Only the changed parts are new; everything else is v1's, under `/v2/`.
  - **The plugin moves to v2.** It reads its own `.nuru/vault-id` and compares UUIDs. Against a service
    that answers `/v2/` with `unsupported_version`, which is today's answer to any other version, it
    says "Update Nuru: this plugin needs a newer Nuru (`git pull` in the Nuru folder)". It never falls
    back to comparing path hashes.
  - **Version tests:** each combination is tested:
    - an old plugin with a new service: chats over v1;
    - a new plugin with an old service: shows the update message and sends nothing;
    - a new plugin with a new service: v2;
    - a v1 client never sees a UUID or `wrong_vault`.
  - **Done when:** a moved vault keeps its ID and its history; a copied one is refused until rebound or
    cloned. The plugin and the service always agree on the ID, and the version tests pass.

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
- **Nothing is assigned automatically.** Today's conversations, service state and logs aren't tagged
  with a vault. Where they go is the user's explicit choice, made by a command: `nuru-serve vault
  adopt-legacy <vault>`, or `--archive` for a read-only archive no vault lists. *(Question for the user,
  below.)* Until it's made, a new-layout service starts with an empty history and says once that the old
  data is waiting.
- **The move is crash-safe.** It's a single command, run with no service running, in these steps:
  1. **Exclusive.** Take `~/.nuru/vaults/.migrate.lock`, and confirm no `nuru-serve` owns the old
     databases. `ServiceStore`'s owner check is used, so a running or managed service makes it stop
     and say so. The plugin's auto-start waits on the lock.
  2. **Checkpoint.** Run `PRAGMA wal_checkpoint(TRUNCATE)` on the old `conversation.db` and `service.db`,
     so every committed row is in the main files.
  3. **Stage.** Copy both, with SQLite's backup API, into `~/.nuru/vaults/<uuid>/.staging/`. Rewrite the
     request keys and `captures.vault_id` from the path hash to the UUID there, in one transaction on
     the staged `service.db`.
  4. **Check together.** Run `PRAGMA integrity_check` on both staged files. Row counts must match the
     old files. Every service-store conversation must point at a conversation that exists in the staged
     conversation store. Any failure deletes `.staging/` and leaves the old files untouched.
  5. **Activate.** Write `migration.json`, `{"state": "staged", …}`, fsync it, then rename `.staging/`
     to the live names. Update it to `{"state": "active"}`, and only then rename the old files to
     `*.pre-wp04`. Logs are copied last; a failure there doesn't undo the move.
  6. **Restart rules.** A service only opens a vault folder whose `migration.json` says `active`, or has
     none, as a new vault does. `staged` with `.staging/` still present means the move never
     activated: `.staging/` is discarded and the old files are used as before. `active` with the old
     files not yet renamed means the move finished but the cleanup didn't: the rename is completed.
     A half-moved vault is never opened.
- **Done when:**
  - after the move, every existing conversation opens with its turns and events;
  - a capture replayed from before the move is still recognised, and nothing is written twice;
  - a crash injected after each step leaves either the old layout working or the new one complete.

### M4 — Phones bound to one vault

- **Pairing** records the vault's UUID in the device's credential: one vault per computer for a phone.
- **When that vault isn't being served,** a capture or chat from the device is refused with v2's 409
  `wrong_vault` (M1) and a retry message, such as "Nuru is serving another vault on this Mac. Try again when
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
Decided with Astra on PR #30:
- **The UUID and `wrong_vault` go in protocol v2,** with v1 frozen (M1).
- **`vault rebind` and `vault clone` stay at the terminal for the first release,** with an explicit source
  and target and a confirmation. The panel explains the conflict and shows the command.
