# `write_note` fixes check, 2026-10-03

This run re-tests the write, refusal and archive prompts (18–33) from [tool-usage-check-2026-10-02.md](tool-usage-check-2026-10-02.md), plus five new prompts, after these changes:

- **`user/` is closed to Nuru.** With `OBSIDIAN_PROTECT_USER_AUTHORED` on (Nuru's own server), `write_note` refuses everything under `user/`.
  - Daily notes moved to `nuru/` (template `0.3.0`): `write_note(type="daily")` writes Nuru's own daily, which is also where reminders go.
  - Your own dailies in `user/` are never written.
- **`mode="append" | "replace"`.**
  - `append` (the default) adds to the end. The description now says "send ONLY the new text".
  - `replace` makes `content` the note's whole text and keeps its frontmatter. Every guard applies to it too.
  - Replacing with nothing is refused, because a delete is a move to trash.
- **Empty notes:** a new note may be empty (`content` is optional). A missing `note` now gets `pass note="<a short title>"`.
- **Plain-words results:** `write_note` and `rename_or_move_file` return a `done` sentence naming the note, its path and what was done.
- **Refusals:**
  - The message for a note you wrote no longer suggests writing a new note in its place.
  - The Modelfile now says to stop and report a refusal, not work around it.
  - Archive wording now says "only move a note out of the archive when the user asks to restore it".

**Setup:** as in the other checks: `granite4.2:3b`, `THINK=low`, the seeded scratch vault (an archived router note and a trashed pancake note), and one turn per prompt with a reset before each. Time is the turn itself.

## Summary

| | Before (2026-10-02) | Now |
|---|---|---|
| Prompts 18–33: right | 8 of 16 | 13 of 16 |
| Prompts 18–33: wrong | 8 | 1 (532) |
| A copy of the whole note sent as `content` | 19, 24, 31, 32 (and 113) | **none**: every append sent only the new text |
| Wrote under `user/` | 24 | **none** |
| Worked around a refusal | 29, 31 | **none**: reported, sometimes offering an option, never acting on it |
| Moved a note out of the archive unasked | 32 | **still 532** |
| Loops or step limits | 21, 23 (418 s) | 530 (stopped as a repeat) |
| Median time, 21 prompts | - | 30.9 s |

**Fixed**
- **Appends.** 519 (Priya), 532 and 533 (router) and 504 (Alice) each sent only the new line, and none duplicated the note.
- **Refusals stay refusals.**
  - **Notes you wrote:** 523 (gym log), 529 (vendor email) and 531 (shower thoughts) were refused. Each time the model reported it, and at most asked whether you'd like an alternative. In the last run it created a copy (31) and an unrequested correction (29).
  - **`user/`:** 524 (directives) refused without even trying the write.
- **The correction (21 → 521) worked.** It first tried `mode="replace"` on the FreeRTOS note. The server refused, because that note is a topic, not a correction. It then created "Freertos Utilisation Correction" with `targets` set to the FreeRTOS note's id. Last time it looped until the repeated-call stop.
- **Daily and reminders.** 518 and 502 went to `nuru/daily-2026-10-03.md`; the reminder (502) became a line in today's daily.
- **`replace` was used as intended.**
  - 505 removed exactly the last code snippet (the ISR) and kept the rest. The diff against the original body shows only the ISR lines and one heading that appeared twice removed.
  - 501 (sort Priya's expertise list) replaced the note with its own text. The list was already alphabetical, which the model noticed and said.

**Still wrong or partly right**
- **532: moved a note out of the archive unasked.** "Add a line to my old router notes" read the archived note, moved it to `nuru/` and then appended. The new wording didn't stop it; the decision needs to move out of the prompt (see the point 4 options).
- **522: the reply blurs created and appended.** The server appended to the existing Alice Chen note (`done`: "Added to the end of the existing note…"), but the reply said "I've added a note titled 'Alice Chen'… stored at nuru/People/alice-chen.md". It's closer than last time's "created", but it doesn't repeat `done`.
- **530: right refusal, slow route.** "Delete my daily from September 17" was refused for being under `user/`, but the turn took 128 s and was stopped as repeating. The refusal message said "which only write_note may write to", which is no longer true on Nuru's server and probably confused the model. That message is fixed now: "the user's own folder: it can't be moved, archived or deleted. Tell the user."
- **503: an "empty" note wasn't quite empty.** The model created "Reading List" with content "Reading List" rather than leaving it blank. Harmless, but not what was asked.
- **520:** still left out `note` on the first try. The new error message got it right on the second call.

## Results

| # | Prompt | Tool calls | Vault | Outcome | Time |
|---|---|---|---|---|---|
| 518 | Add 'call mom for her birthday' to today's daily | write_note(type=daily) | + nuru/daily-2026-10-03.md | right | 31.4 s |
| 519 | Add to Priya's note: prefers async updates on Fridays | search, read_note, write_note(append, new line only) | ~ priya-raman.md (+1 line) | right (was wrong) | 30.9 s |
| 520 | Create a note for the Lisbon trip plan | write_note! (no note), write_note | + shared/lisbon-trip-plan.md | right after one error | 12.3 s |
| 521 | FreeRTOS note says 0.58 but the table adds to 0.36; write a correction | search, read_note, write_note! (replace on the original), write_note(correction, targets) | + nuru/freertos-utilisation-correction.md | right after one error (was a failure) | 110.4 s |
| 522 | Make a note called Alice Chen with her new number | write_note | ~ People/alice-chen.md (+1 line) | appended correctly; reply blurs created and appended | 8.6 s |
| 523 | Add "skipped leg day" to my gym log (yours) | search, read_note, write_note! | - | right: refused, reported (was a 418 s loop) | 77.4 s |
| 524 | Add a rule to my directives (user/) | search, read_note | - | right: refused, nothing written (was wrong) | 143.4 s |
| 525 | Rename Bob Martinez to Robert Martinez | search, rename_or_move_file | renamed | right | 19.6 s |
| 526 | Move the memfault note into shared/Done | search, rename_or_move_file | moved | right | 24.8 s |
| 527 | Archive the one-on-one with manager note | search, rename_or_move_file(archive) | → archive/ | right | 16.0 s |
| 528 | Delete the UART ring buffer snippets note | search, rename_or_move_file(trash) | → trash/ | right | 19.9 s |
| 529 | Delete the vendor email note (yours) | search, rename_or_move_file! | - | right: refused, reported (was an unrequested correction) | 48.6 s |
| 530 | Delete my daily from September 17 (user/) | search, rename_or_move_file! | - | refused correctly; slow and stopped as repeating; refusal message now fixed | 128.4 s |
| 531 | Rename my shower thoughts note (yours) | search, rename_or_move_file! | - | right: refused, offered an option (was a copy) | 28.7 s |
| 532 | Add a line to my old router notes (archived) | search, read_note, rename_or_move_file (out of archive), write_note, read_note | archive → nuru/, +1 line | **wrong**: restored without being asked (appended the new line only) | 55.7 s |
| 533 | Restore the router notes from the archive and add the replacement date | search, rename_or_move_file, read_note, write_note, read_note | archive → nuru/, +1 line | right | 63.8 s |
| 501 | Sort the items under Expertise in Priya's note | search, read_note, write_note(replace) | unchanged (already sorted) | right | 102.3 s |
| 502 | Remind me to order the nylon standoffs | write_note(type=daily) | + nuru/daily-2026-10-03.md | right | 9.7 s |
| 503 | Create an empty note called Reading List | write_note(note, content="Reading List") | + nuru/reading-list.md | created, but not empty | 7.7 s |
| 504 | Add to Alice's note that she loves espresso | search, read_note, write_note(append) | ~ People/alice-chen.md (+1 line) | right | 27.8 s |
| 505 | Remove the last code snippet from my UART note | search, read_note, write_note(replace) | ~ uart-ring-buffer-snippets.md | right: only the ISR snippet removed | 98.3 s |
