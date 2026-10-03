# `read_note(part=...)` check, 2026-10-03

`note_info` is now `read_note(name, section=None, part="content" | "info")`, with `"content"` as the default.

- `"content"` returns the note's plain Markdown, as before; `section` still works.
- `"info"` returns what `note_info` returned: tags, outgoing links, backlinks and open tasks, without the text.

The text stays plain Markdown rather than a JSON string, so quotes and `replace` writes don't pick up escaped characters. Nuru now offers the model 10 tools. Nuru's own attachment resolution for `ask_an_expert` now calls `read_note(part="info")`.

**Setup:** as in the other checks: `granite4.2:3b`, `THINK=low`, the seeded scratch vault, and one turn per prompt with a reset before each. Prompts 601–608 are 401–408 from [note-info-check-2026-10-02.md](note-info-check-2026-10-02.md); 611–614 test plain reading and a `replace` write.

## Summary

- **`part="info"` was used less than `note_info` was.** It came up on 2 of 8 prompts (604 tags, 605 overview), against 5 of 8 for the separate `note_info` tool.
  - On links (602, 606), open tasks (603) and backlinks (601), the model read the text instead.
  - Its answers were still right, except 601 and 607, which were partly right (see below). It worked them out from the text, not from `info`'s resolved links.
  - With one tool, the model reaches for the default.
- **Plain reading is unaffected and clean.**
  - 612 returned only the requested section.
  - 613 quoted the gym log's sleep line exactly, with no escape characters.
  - 611 read the right note but then wandered to a transcript for its answer. It's a reading-judgement slip, not a tool problem.
- **601 (backlinks) and 607 (vault-wide broken links)** were answered from `find_in_notes` and from text the notes contain, not from link resolution. They were partly right, as in the previous run.
- **614 is a regression, and an important one.** The prompt was "remove the last code snippet". The model read the note, then called `write_note` with the full note text but without `mode="replace"`. The default `append` added the whole note to itself again. The model then said the snippet was removed. In the previous run (505) it remembered `mode="replace"` and got it right. The prompt wording alone doesn't reliably prevent a whole-note append, so the server needs a guard (see below).
- **Median time 24.6 s**; 614 took 181 s and was steered twice.

## What follows

1. **A server guard against whole-note appends.** `write_note` in append mode will refuse `content` that repeats the note's existing text, saying: "content repeats the note; to change it use mode='replace' with the complete new text; to add, send only the new text". That would have stopped 614 and the 40-prompt run's 19, 24, 31 and 32. This is being added with the `write_note` changes now.
2. **`part="info"` vs a separate `note_info`:** `info` gets used less inside `read_note`. If its information matters, the options are a sharper description, or restoring `note_info` as its own tool (11 tools). The answers were mostly still right without it.

## Results

| # | Prompt | Tool calls | Outcome | Time |
|---|---|---|---|---|
| 601 | Which notes link to the Area Monitor Device Redesign project? | search, find_in_notes | partly right: from find_in_notes, not backlinks | 52.6 s |
| 602 | What does my technical knowledge base note link to? | search, read_note | right, from the text | 20.9 s |
| 603 | Open tasks left in the architecture review transcript | search, read_note | right, from the text | 24.0 s |
| 604 | Tags on my sensor calibration note | search, read_note(part=info) | right | 17.9 s |
| 605 | Overview of my battery note (tags, links, tasks) without reading it | search, read_note(part=info) | right | 19.2 s |
| 606 | Are any links in my knowledge base note broken? | search, read_note | right, from the note's own text | 22.6 s |
| 607 | Any broken links anywhere in my vault? | search, read_note ×2 | partly right: no tool for this | 48.0 s |
| 608 | Notes related to my FreeRTOS note, and what they're about | search, read_note ×3 | right | 59.3 s |
| 611 | What did I decide about the modem configuration? | search, read_note, search, read_note | read the right note, then answered from a transcript | 57.7 s |
| 612 | Just the Reorder list section of my inventory | search, read_note(section) | right | 21.6 s |
| 613 | Quote the gym log's average-sleep line exactly | search, read_note | right: exact quote, no escapes | 25.1 s |
| 614 | Remove the last code snippet from my UART note | search, read_note, write_note (no mode: append) | **wrong**: appended the whole note again; reply claimed the snippet was removed | 181.4 s |
