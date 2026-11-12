# R14: conversation history implementation plan

Status: implementation preparation, 2026-11-12; no runtime changes in this document.
Base: main `46dcc967a79643025499c761af09f0402db068ab` (F33 and F25 merged).

## Directly confirmed product behavior

The user confirmed all four numbered F31/R14 choices on 2026-11-12. R14's choices:

- Delete hides a conversation and offers Undo. After 30 days hidden, purge its
  conversation text permanently while preserving required change evidence and
  request receipts/tombstones. No vault notes are deleted or reverted by this action.
- Search titles, user prompts and replies using literal, case-insensitive matching,
  bounded by principal and this service's vault. Exclude hidden conversations from
  ordinary search; use an explicit Hidden filter for recovery during retention.
- Refuse hiding while a turn is active/queued or a title job is pending/running.
  Preserve request receipts when suppressing or cleaning zero-turn conversations.
- Renaming is explicit user intent and must win over late generated titles. F25's
  title backfill already exists and remains available; no second generation path.

No more user choice is needed for these four points. Storage details below are
implementation proposals for Claude's review, not additional product requirements.

## Preservation and authorization

Add hidden/purge metadata to ServiceStore conversation rows. Keep identity/ownership
stubs for purged conversations and their terminal turn IDs so foreign references,
write attempts and request replay stay meaningful. Never cascade-delete the parent
ConversationStore conversation or its write ledger to remove UI history.

Ordinary list/detail/send/title endpoints exclude or refuse hidden/purged resources;
only an explicitly authorized Hidden view/Undo can restore visibility before expiry.
IDs are not authority: every endpoint checks principal/vault and remote read/write
scopes as appropriate, including code-owned request bindings. No model tool for
conversation hiding/purging is introduced by R14.

Hiding, Undo and rename use client request IDs and the existing idempotency layer.
After purge, an old accepted create/submit ID remains consumed; replay cannot create
or run new work. Lost-response settlement must report the hidden/purged state rather
than silently unhiding or resubmitting. Undo after retention cannot recover text that
was purged and must say so plainly.

## Rename and title generation races

Use a title revision plus explicit-user-name metadata, not just equality of strings.
A user can rename to the same string or to a prefix of the first prompt; neither
should invite the automatic first-turn title writer to overwrite that choice.

Rename accepts an expected revision and returns a conflict on a stale edit. Both
queued explicit title requests and automatic first-turn naming capture the revision
and store only if unchanged. Explicitly clicking Generate title may replace the
current name, subject to that revision guard; opening history must not start a model.

## Search, empty rows and navigation

Use bound SQL parameters and literal matching (escape LIKE wildcards or use a literal
substring function); do not interpret regex, SQL or FTS query syntax from the user.
Bound query length, result count and pagination. Search full persisted reply text,
not only truncated picker labels, while returning bounded metadata and not unrelated
note content. Define/test Unicode case behavior rather than claiming SQLite lower()
provides full Unicode casefolding automatically.

Never create a server conversation merely by pressing New or navigating. The plugin
already creates at first send; a successful create followed by failed submit can
leave a zero-turn row. Suppress these in ordinary history while retaining consumed
request IDs/identity until request reconciliation is safe. Ensure accepted sends
refresh the list and keep the correct selection even if a zero-turn row was omitted.

Protect list/detail/search responses with navigation generations. Search or hiding
must not steal input focus, discard drafts or apply stale results after navigation.
Expose hidden recovery and expired-text status honestly; Delete's dialog explains
Undo/30-day purge and never implies that logs, backups or vault notes are erased.

## Purge inventory and recovery protocol

Content exists in more than one place:

| Location | Purge treatment |
| --- | --- |
| ServiceStore conversation title, turn text and final Display | Remove conversation text after retention; retain minimal identity/state stubs |
| Stored turn events/questions | Remove content-bearing event/question records once no live work references them |
| ConversationStore title/messages | Remove corresponding conversation text; preserve parent identity and write_attempts |
| Request receipts | Keep binding hashes and consumed-ID tombstones; audit bodies for free-text answers and redact content-bearing cached responses without ever re-executing requests |
| Write attempts/F31 journal links | Preserve required mutation/reconciliation evidence and authorization links, including uncertain attempts; do not claim these necessarily contain no note bytes |
| Existing full-content logs and OS backups | Separate retention policies; not silently removed by R14 and not advertised as secure erasure |

Mark a conversation purging before scrubbing either store so it cannot be opened,
unhidden, renamed or submitted to mid-purge. Use durable idempotent phases across the
two SQLite databases; a process crash resumes cleanup rather than exposes half-erased
history or repeats a model/write action. Do not assume two separate WAL databases
have one atomic commit. Recheck that no active/queued/title work can reference it.

Run bounded maintenance batches, with a clock injectable in tests. Hidden retention
starts at the hide timestamp; Undo before the deadline clears it. Re-hiding starts a
new deadline. Define the exact boundary at 30 days and check expiry inside Undo,
not only when a maintenance job happens. No recursive file removal, log deletion,
large backups in Git or automatic vault mutation.

## Verification and implementation order

1. Store migrations and model-free tests for ownership, revision races, hidden/Undo,
   empty-history suppression, literal search and retention boundaries.
2. Host routes and request replay; busy guards; title-job revision integration;
   remote credentials/CSRF/revocation; crash-resumable content purge preserving evidence.
3. Client/history UI with search, rename, hide confirmation, Undo/Hidden filter and
   explicit expired-text messages; generation/focus/draft protections.
4. HTTP/plugin integration for lost replies, repeated request IDs, wrong owner,
   non-ASCII/search wildcards, zero-turn create/submit failures, title races,
   interrupted purge and history reopened after restart.
5. Full local suite/build and combined coverage gate; hosted Windows-specific tests
   only. Useful mutation checks must catch scope bypass, replayed work, ledger deletion,
   early/late expiry, same-string rename overwritten and stale navigation.

Claude reviews security, retention/idempotency and UX before merge. Preserve existing
parked branches and F31 design #90; R14 does not implement Restore or weaken its rules.
