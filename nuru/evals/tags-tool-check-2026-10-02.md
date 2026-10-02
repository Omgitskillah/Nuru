# `tags` tool check, 2026-10-02

`all_tags`, `notes_with_tag` and `note_tags` are now one model tool, `tags(tag=None, note=None)`:

- with no arguments, it lists every tag in use;
- with `tag`, the notes carrying that tag;
- with `note`, that note's tags;
- passing both is refused.

Nuru now offers the model 14 tools.

**Setup:** the same as the [search](search-tool-check-2026-10-02.md) and [browse](browse-tool-check-2026-10-02.md) checks: `granite4.2:3b`, `THINK=low`, the seeded scratch vault, and one turn per prompt with a reset before each. Time is the turn itself.

## Summary

All 8 prompts used `tags` in the intended mode and got the right answer:

- no arguments for the most-used tags (301);
- `tag` with or without `#` (302, 303, 307);
- `note` after a search for the note's path (304, 305);
- a tag that doesn't exist (306).

On 306 the server suggested "Did you mean: recipe". The model reported that no note is tagged "recipes" and offered to look further, rather than retrying with "recipe". That's acceptable, if one step short. The combined prompt (308) went `tags(tag="power")`, then read both power notes, and quoted the 3-year requirement.

Median time 17.0 s (9.3–72.3 s).

| # | Prompt | Tool calls | Outcome | Time |
|---|---|---|---|---|
| 301 | Which tags do I use most? | tags() | right: sample 22, topic 14, analysis-test 12 … | 23.6 s |
| 302 | Notes tagged analysis-test | tags(tag=analysis-test) | right: 12 notes | 16.9 s |
| 303 | Notes tagged #embedded | tags(tag=embedded) | right | 9.3 s |
| 304 | Tags on my sensor calibration note | search, tags(note=nuru/sensor-calibration-data.md) | right | 25.1 s |
| 305 | How is my FreeRTOS note tagged? | search, tags(note=…/freertos-task-management.md) | right | 17.1 s |
| 306 | Notes tagged recipes | tags(tag=recipes): "did you mean: recipe" | said none; offered to look further, didn't retry "recipe" | 12.4 s |
| 307 | Any notes tagged personal? | tags(tag=personal) | right: 2 notes | 15.9 s |
| 308 | Notes tagged power: what's the battery requirement? | tags(tag=power), read_note ×2 | right: the 3-year requirement, quoted | 72.3 s |
