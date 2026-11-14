# R14: conversation history implementation plan

Status: integration in progress, 2026-11-14. Storage foundation #91 is merged;
search, title revision guards and host/API mutations are on codex/r14-host-integration.
The UI and permanent-erasure flow remain unfinished.

## Directly confirmed behavior

On 2026-11-14 the user directly confirmed replacing the earlier 30-day automatic
purge policy with manual Empty trash. This supersedes the earlier retention choice.

- Delete moves a conversation to Trash. Restore is available without an age limit.
- Show Trash (N) when nonempty as a quiet reminder; no timed prompts or nagging.
- Empty trash permanently erases the conversations in the confirmed Trash snapshot,
  only after a code-owned confirmation. Explain that identity stubs, consumed request
  IDs and required write evidence remain, and logs/OS backups are not erased.
- No age-selected cleanup, scheduled purge timer or deadline on restoration.
- Resume an interrupted erasure at startup only if the user already confirmed it.
- Refuse hiding or erasing while active, queued or title work references the targets.
- Search titles, user prompts and replies with literal Unicode casefold matching,
  scoped to the authenticated principal and this service's vault.
- Manual names win over late generated titles, including same-string renames.

This product choice does not authorize erasing any current user data during development.
Use disposable databases and vaults in tests. No model tool for Empty trash is added.

## Authorization and replay

Read/search requires chat:use + notes:read remotely. History mutations, including
Generate title, require a separately granted history:write scope. Existing device
grants are not silently upgraded. Pairing and CLI explain the additional permission.
The separately relayed decision to require re-pairing remains pending direct user
confirmation; do not infer credential migration authorization from discussion data.

Host ownership checks, current-grant rechecks, CSRF and revocation enforce permissions
independently of button visibility. A missing scope gets a short explanation on the UI.

Hide/Restore/rename use request IDs and persisted receipts. A replay never repeats an
old mutation after restoration, re-hiding or renaming; return the current state instead.
Old accepted create/send IDs stay consumed after erasure. Hidden/erasing/erased resources
cannot be used by ordinary detail, events, send or title endpoints. Settlement reports
the unavailable state without resubmitting a model turn or silently restoring history.

## Controls and navigation

Keep the conversation picker and New/Title actions; add labelled Search and Clear,
Rename and Delete actions, and Trash (N). Rename opens a text dialog. Delete explains
that it moves history to Trash and offers Restore. Trash permits restoration without a
deadline. Empty trash opens the explicit irreversible-erasure confirmation described
above. Single-conversation permanent erasure is omitted unless it becomes necessary.

No conversation is created merely by pressing New. Suppress zero-turn rows in ordinary
history while preserving identity and request receipts. Preserve drafts, selection,
focus and navigation generations; stale search/hide/restore replies cannot overwrite
a newer selection. Describe scope failures before the user attempts a forbidden action.

## Title and worker races

Both generated-title paths capture and compare title revisions. Automatic first-turn
naming additionally skips manual titles and rechecks title_by_user at application.
Explicit Title may replace a manual name but cannot overwrite a later rename.

Hide checks queued/running title jobs and the actual worker lifetime under the same
service lock as the mutation and receipt. A terminal event can free the queue slot
before a generator finishes unwinding; _working_conversation guards this interval.
A hidden row cannot start automatic title generation or receive its late result.

## Permanent-erasure protocol to implement

Prepare an ownership-bound confirmation for an explicit snapshot of Trash. Bind the
confirmation to the snapshot version so newly trashed, restored or re-hidden resources
cannot be silently added or erased after a stale confirmation. The UI must say the
number being erased; cancellation makes no change. Revalidate ownership, grants and
busy state before accepting it. Replayed confirmation requests must not start another
erasure or broaden the snapshot.

Before scrubbing, persist the purging mark and durable user-confirmed intent. Two WAL
SQLite databases are not one atomic transaction: phases must be idempotent and crash
resumable. Startup may resume existing marked work, never mark old Trash due to age.
Use bounded batches; do not hold a model call or live-vault write during erasure.

| Location | Treatment after confirmed Empty trash |
| --- | --- |
| ServiceStore conversation title, turns.text and turns.final | Remove text; retain minimal ownership/turn identity stubs |
| Turn events/questions | Remove content-bearing history without breaking required write receipts |
| ConversationStore title/messages | Remove text; retain conversation identity and write_attempts |
| Request receipts | Preserve key/binding hashes and consumed IDs; scrub content-bearing cached responses without replaying work |
| Write attempts and F31 evidence links | Preserve required mutation/reconciliation evidence, including uncertain attempts that may contain note bytes |
| Full-content logs and OS backups | Separate policies; never advertise these as erased by Empty trash |

No cascading parent deletion, recursive file removal, vault-note deletion, large Git
backups or changes to unrelated data. Mark purged_at only after both stores are scrubbed.
Until then, purging prevents reading or restoring a partly erased conversation.

## Verification and review

Storage tests cover upgrade/reopen, literal non-ASCII and wildcard queries, owner/cursor
isolation, empty rows and preserved receipts. Tests formerly asserting 30-day expiry
must instead assert restoration after arbitrarily long time and no age-started purge;
this expectation change is required by the user's 2026-11-14 policy replacement.

Host/HTTP tests cover queued/running/automatic title races, terminal worker unwinding,
request replay, same-string renames, hidden detail/events, strict schemas, explicit
history grants, CSRF and revocation between authentication and mutation. The changed
remote-title test explicitly grants history:write because title generation renames
history; read authority alone is insufficient.

Erasure tests must inject crashes between each durable phase, preserve other owners
and active/uncertain write evidence, reject stale confirmations, exercise restart,
replay and new Trash entries during a batch, and prove cancellation changes nothing.
UI tests cover Trash counts, restored history, stale navigation, retained drafts/focus
and clear confirmation/permission explanations. Use useful mutation probes for each
security or preservation boundary, not coverage-only assertions.

Run full local tests/build and the combined coverage gate before integration approval.
Use hosted CI only where Windows validation is necessary. Claude reviews the complete
slice before merge. F31 #90 is a merged design; its implementation remains queued and
its capacity proposals are separate from R14's Trash policy.
