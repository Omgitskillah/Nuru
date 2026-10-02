# `note_info` check, 2026-10-02

`get_links` and `note_todos` are now one model tool, `note_info(name)`, which also returns the note's tags. It gives everything around one note except its text, which stays with `read_note`: `{note, tags, outgoing, backlinks, todos, todo_count, hint}`. The name is resolved once, so every part describes the same note.

`find_broken_links` is off the model's tools. The plan is a system job that fixes or flags broken links on its own; it's kept as reminder 15. Its scanning code stays in the server as an internal function, as the starting point. Nuru now offers the model 12 tools:

- `browse`, `search`, `find_in_notes`, `read_note`, `note_info`, `tags`
- `write_note`, `rename_or_move_file`
- `calculate`, `statistics`
- `ask_an_expert`, `classify_notes`

Nuru's own attachment resolution for `ask_an_expert` used `get_links` to turn a note title into a path; it now calls `note_info`.

**Setup:** as in the other checks of 2026-10-02: `granite4.2:3b`, `THINK=low`, the seeded scratch vault, and one turn per prompt with a reset before each. Time is the turn itself.

## Summary

- **5 of 8 used `note_info`:** backlinks (401), outgoing links (402), open tasks (403), tags (404) and the full overview (405). All right.
- **401** first tried `find_in_notes` for `[[Area Monitor Device Redesign]]`, then called `note_info`. One extra step.
- **406, "are any links in this note broken?":** the model read the note instead of calling `note_info`. Its answer was right, because the note itself says those links don't exist yet. With `note_info`, the `path: null` entries would have shown it without relying on that.
- **407, broken links across the vault:** there is no tool for this any more. The model used `find_in_notes("\[\[")` and inferred the broken links from what the notes say. The answer was partly right. This is the case the planned system job should handle.
- **408, "related notes and what they're about":** the model read the note and the knowledge-base index rather than starting from `note_info`'s links. A reasonable route, and the answer was right.
- **Median time 23.9 s** (16.3–56.1 s).

| # | Prompt | Tool calls | Outcome | Time |
|---|---|---|---|---|
| 401 | Which notes link to the Area Monitor Device Redesign project? | search, find_in_notes, note_info | right (one extra step) | 49.6 s |
| 402 | What does my technical knowledge base note link to? | search, note_info | right, broken links included | 20.6 s |
| 403 | Open tasks left in the architecture review transcript | search, note_info | right: the open checkboxes with line numbers | 27.1 s |
| 404 | Tags on my sensor calibration note | search, note_info | right | 16.9 s |
| 405 | Overview of my battery life note (tags, links, open tasks) without reading it | search, note_info | right | 16.3 s |
| 406 | Are any links in my technical knowledge base note broken? | search, read_note | right, from the note's text rather than note_info | 17.3 s |
| 407 | Any broken links anywhere in my vault? | search, find_in_notes | partly right: no tool for this any more | 42.0 s |
| 408 | Notes related to my FreeRTOS note, and what they're about | search, read_note ×2 | right, without note_info | 56.1 s |
