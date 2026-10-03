# `read_note` with `info` as the default, 2026-10-03

This is a trial following [read-note-part-check-2026-10-03.md](read-note-part-check-2026-10-03.md). There, `part="content"` was the default, and the model asked for `part="info"` on only 2 of 8 prompts that were about a note's tags, links or tasks. Here the default is reversed:

- `read_note(name)` returns the note's tags, links, backlinks and open tasks;
- the text needs `part="content"`.

The tool description and the Modelfile say the same. The 12 prompts are the same as before (801–814 = 601–614), with the same setup: `granite4.2:3b`, `THINK=low`, the seeded scratch vault, one turn per prompt with a reset before each, and a scratch decision database per run.

## Result: it made `info` use worse, not better

| | `content` default (601–614) | `info` default (801–814) |
|---|---|---|
| `info` used, on the 8 link, tag and task prompts | 2 of 8 | **0 of 8** |
| `read_note` calls passing `part="content"` explicitly | - | 12 of 12, every read |
| Answers right on those 8 | 6 (2 partly) | 6 (2 partly), from the text each time |
| Median time | 24.6 s | 33.9 s |

With `info` as the default, the model put `part="content"` on every single `read_note`. That includes 804 (a note's tags) and 805 (an overview without reading the note), which used `info` last time.

The model doesn't weigh the two parts; it learned "pass content". Reading the text is what it trusts, and it gets the tags, links and tasks out of the text. The answers were as good as before (802, 803 and 806 correctly listed links, tasks and broken links from the text), just slower.

**Recommendation:**
- put the default back to `content`, which is what the model does anyway;
- either keep `part="info"` as the rarely used option, or give `note_info` back as a separate tool (it was used on 5 of 8 prompts in [note-info-check-2026-10-02.md](note-info-check-2026-10-02.md)).

The trade-off is one more tool (11), for information the model can mostly get from the text.

## 814: the duplicate guard worked, and a false claim remains

The prompt was "remove the last code snippet" in the UART note: the same prompt as 614, which duplicated the note.

1. The model read the note. Its thinking hit the budget and was steered three times while it went back and forth on what "the last snippet" means: the note has one code block containing a struct, functions and an ISR.
2. It called `write_note` with the whole note and no `mode`. **The new guard refused it:** "content repeats the text already in … call again with mode="replace" and the complete new text". The note was not duplicated.
3. It called `write_note(mode="replace")` with the note's text exactly as it was, ISR still in. The note was rewritten with identical text, so nothing changed.
4. The reply said: "The note has been updated to remove the last code snippet as requested." That is false. The turn took 340 s.

The corruption is gone, but the false claim isn't. A small server fix would expose it: a `replace` whose text is identical to the note's current text should change nothing and say so. The `done` sentence would read "Nothing changed: the new text is the same as the note's", and the model would have to deal with that rather than report success.

## Results

| # | Prompt | Tool calls | Outcome | Time |
|---|---|---|---|---|
| 801 | Which notes link to the Area Monitor Device Redesign project? | search, find_in_notes | partly right (as 601) | 39.9 s |
| 802 | What does my knowledge base note link to? | search, read_note(content) | right | 20.4 s |
| 803 | Open tasks in the architecture review transcript | search, read_note(content) | right | 22.5 s |
| 804 | Tags on my sensor calibration note | search, read_note(content) | right (used info last time) | 16.2 s |
| 805 | Overview of my battery note without reading it | search, read_note(content) | right, but read the text, which the prompt said not to | 24.1 s |
| 806 | Are any links in my knowledge base note broken? | search, read_note(content) | right | 20.7 s |
| 807 | Any broken links anywhere in my vault? | search, find_in_notes, read_note(content) ×2 | partly right | 72.8 s |
| 808 | Notes related to my FreeRTOS note | search, read_note(content) ×3 | right | 61.6 s |
| 811 | What did I decide about the modem configuration? | search, read_note(content) | right (better than 611) | 38.6 s |
| 812 | Just the Reorder list section | search, read_note(content), with no `section` | right: picked the section out itself | 30.9 s |
| 813 | Quote the gym log's average-sleep line | search, read_note(content) | right: exact quote | 36.9 s |
| 814 | Remove the last code snippet from my UART note | search, read_note(content), write_note! (refused), write_note(replace, unchanged text) | **wrong**: nothing removed, success claimed; no duplication | 339.6 s |
