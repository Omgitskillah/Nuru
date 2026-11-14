# R14: Check by hand

Use an isolated test vault with disposable conversations. Do not empty personal history
while checking the feature. Run this on macOS and Windows before release; automated
validation does not mean these manual checks have been performed.

- Open History while a draft is partly typed. Search titles, prompts and replies with
  non-ASCII text, `%` and `_`; try rapid successive queries. Keep the input focus and
  selection, then close History and verify the chat draft is unchanged.
- Open a result and check the selected picker item and message history match. Rename
  it, including renaming to its current name. A late generated title must not win.
- Delete a conversation, first cancelling the dialog and then accepting. Confirm it
  moves to Trash, the count changes, vault notes stay unchanged, and Restore works.
- Try Delete while a turn or title job is running. It should explain that it is busy.
- Search normal history and Trash independently; inspect enough entries to load more.
  Hidden zero-turn conversations should be recoverable and match the Trash count.
- In Trash, choose Empty trash. Inspect the named list and explanation of retained
  write evidence/request identities/logs/backups. Cancel: nothing is erased.
- Using only disposable history, accept Empty trash. Verify erased/kept counts and
  that Nuru cannot restore erased conversation text. Restore and re-delete an item
  from another device while the dialog is open: the stale confirmation must keep it.
  Trash a new conversation after opening the dialog: it must not be included.
- Simulate a lost response or close the panel during a multi-batch operation. Reopen
  History and use Check pending erasure. Confirm the existing request settles rather
  than creating another confirmation or adding new Trash. For a reported pending
  backend erasure, release any database contention and check its receipt again without restarting Nuru. Confirm it completes; a finishing turn also retries pending erasure.
- Pair a read/search-only phone/browser. History search and reading work; Title,
  Rename, Delete, Restore and Empty trash are disabled with a re-pair explanation.
  Explicitly re-pair with history:write and verify those actions become available.
  Revoke that grant/session while History is open: the server must refuse mutations
  and the dialog should explain the permission issue.
- Check narrow sidebar widths, keyboard access, focus returning from dialogs,
  readable long titles, and that the permanent-erasure dialog starts on Cancel.

No release or merge approval is implied by this checklist. Record actual observations
and any failures in the PR; do not tick off steps that were not performed.
