# `browse` tool check, 2026-10-02

`list_notes`, `list_files`, `list_files_in_directory` and `browse_notes` are now one model tool, `browse(type, limit, folder)`. It lists notes newest first, with an optional type filter and an optional folder. A folder is found by path, or by name anywhere in the vault. With a folder, the folder's attachments come back under `files`. `trash/` is never listed. Nuru now offers the model 16 tools.

**Setup:** the same as [search-tool-check-2026-10-02.md](search-tool-check-2026-10-02.md): `granite4.2:3b`, `THINK=low`, the seeded scratch vault, and one turn per prompt with a reset before each. Time is the turn itself.

## Summary

- **11 of 12 prompts used `browse` as intended:**
  - newest notes (201);
  - type filter (202, 208);
  - folder by path (204) and by name (205);
  - archive (209), trash (210) and a missing folder (211);
  - the whole vault, with `limit` raised to 50 (207).
- **203 used `search("person")`** rather than `browse(type="person")`. It found all three people anyway.
- **Folders resolved by name:** `browse(folder="Transcripts")` found `nuru/Transcripts`.
- **Trash looks like no folder at all:** `browse(folder="trash")` returned the same "No directory matching" hint as a folder that doesn't exist. The reply was "There are no notes in your trash folder."
- **Archive shows its notes:** `browse(folder="archive")` listed the archived router note.
- **Classify (212) now stays in the folder.** In the previous run, `browse_notes(type="topic")` pulled in notes from everywhere; `browse(folder="nuru/Topics")` returns just its 2 notes. The model still read them, though, instead of calling `classify_notes`.
- **206 asked for project notes in `shared/`.** The model browsed the folder without `type="project"`, then picked out the project notes itself. The answer was right.
- **Median time 13.4 s** (6.6–77.7 s); the earlier listing prompts took 18–33 s.

## Results

| # | Prompt | Tool calls | Outcome | Time |
|---|---|---|---|---|
| 201 | What are my most recent notes? | browse(limit=10): 41 notes | right | 33.5 s |
| 202 | Latest daily note and what's in it? | browse(type=daily): 4, read_note | right: 2026-09-18 | 29.7 s |
| 203 | Which people do I have notes about? | search("person") | right: Alice, Bob, Priya (search, not browse) | 14.2 s |
| 204 | What's in my nuru/People folder? | browse(folder=nuru/People): 2 | right | 6.6 s |
| 205 | Notes in my Transcripts folder | browse(folder=Transcripts): 2 | right: found by name | 8.2 s |
| 206 | Which project notes are in shared? | browse(folder=shared): 5 | right (no type filter; the model picked out the projects) | 12.5 s |
| 207 | List every note in my vault | browse(limit=50): 41 | right | 77.7 s |
| 208 | What was my most recent working session about? | browse(type=session): 3, read_note | right | 42.3 s |
| 209 | What have I archived? | browse(folder=archive): 1 | right | 11.1 s |
| 210 | What's in my trash folder? | browse(folder=trash): 0, "no such directory" | right: trash invisible | 8.6 s |
| 211 | Notes in my Recipes folder? | browse(folder=Recipes): 0, hint | right | 8.5 s |
| 212 | Without reading them, sort nuru/Topics into work and personal | browse(folder=nuru/Topics): 2, read_note ×2 | right folder; still read the notes rather than call classify_notes | 60.2 s |
