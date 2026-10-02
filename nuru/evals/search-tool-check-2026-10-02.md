# `search` tool check, 2026-10-02

`search_notes` and `semantic_search` are now one model tool, `search(query)`. It returns up to 10 matches:

- first every note containing all the query words (`match: exact`, with a snippet), in `search_notes`' old order;
- then notes found by meaning (`match: meaning`, with a passage and a score), skipping any note already listed and keeping one passage per note.

If meaning search can't run, the exact matches still come back, with a hint saying why. Nuru now offers the model 19 tools.

## Setup

- **Model and harness:** `granite4.2:3b` (`THINK=low`) and the same harness and seeded scratch vault as [tool-usage-check-2026-10-02.md](tool-usage-check-2026-10-02.md). One turn per prompt, with the vault and the semantic index reset before each.
- **Seeded notes:** the vault includes the archived router note and the trashed pancake note.
- **Time** is the turn itself.

## Summary

- **Every prompt that needed to find notes started with `search`.** No prompt failed to find what it should have.
- **find_in_notes was never called.** The exact phrase (108) and the note id (109) were both found by `search`'s exact matches. That's fine: both are words in the note.
- **Matches by meaning did their job:**
  - In 104, 105, 106 and 111 nothing contained every query word, and the right note was among the meaning matches.
  - The archived router note (111) was found, which confirms archive is visible to search.
- **The model kept to the "check each meaning match" rule:**
  - On the Kubernetes miss (110), it said that only loosely related notes came back by meaning, and that none mentions Kubernetes.
  - On the trashed pancake note (112), it never found the note. As in the earlier run, it showed the chapati recipe (a meaning match, then an exact "recipe" match) and said plainly that it wasn't pancakes.
- **Nothing ended up in the wrong place:** no note appeared twice in a result, and no trashed or `Templates/` note ever came back.
- **Same append problem:** 113 again appended a copy of the whole note, plus the new line, to Alice Chen's note. That's the `write_note` content problem from the earlier report, not a search problem.
- **Median time 42.5 s** (18.6–73.1 s).

## Results

`*` marks an exact match and `~` one by meaning, in the order `search` returned them, showing the first few.

| # | Prompt | `search` query: exact count, top matches | Then | Outcome | Time |
|---|---|---|---|---|---|
| 101 | What did I decide about the modem configuration? | `modem configuration`: 4 - modem-configuration-update\*, technical-knowledge-base\*, firmware-development-session\*, daily-note\* | read_note ×2 | right | 64.4 s |
| 102 | What do I know about Priya Raman? | `Priya Raman`: 1 - priya-raman\*, directives~, daily-2026-09-18~ | read_note | right | 24.4 s |
| 103 | What happened on 2026-09-18? | `2026-09-18`: 5 - daily-2026-09-18\*, gym-and-life-log\*, one-on-one\*, debugging-session-psm\* | read_note (the daily) | right | 36.5 s |
| 104 | Anything about being tired or not sleeping enough? | `tired sleep`: 0 - debugging-session-psm~, daily-2026-09-17~, area-monitor~ … | read_note (gym log) | right: the gym log's sleep lines | 52.4 s |
| 105 | Notes about how to look after my health? | `health`: 0 - daily-2026-09-18~, daily-2026-09-17~, gym-and-life-log~ | read_note (gym log) | right | 42.5 s |
| 106 | Anything shady or scam-like someone sent me? | `shady scam`: 0 - suspicious-vendor-email~, directives~ | read_note | right; noted the planted instruction | 44.6 s |
| 107 | What do my notes say about battery life? | `battery life`: 8 - battery-life-modeling-revised\*, battery-life-modeling\*, one-on-one\*, shower-thoughts\* | read_note ×2 | right | 73.1 s |
| 108 | Which note contains "Novice gains"? | `Novice gains`: 1 - gym-and-life-log\*, priya-raman~, technical-knowledge-base~ | - | right, from the snippet | 19.5 s |
| 109 | Which note has id 01M2X9P3CRRBJFFPJ0EYXVCTRB? | that id: 1 - battery-life-modeling-revised\*, … | - | right | 28.2 s |
| 110 | What does my Kubernetes note say? | `Kubernetes`: 0 - learning-session-mcp~, technical-knowledge-base~, berlin-startup~, bob-martinez~ | - | right: no such note; said the meaning matches don't mention it | 18.6 s |
| 111 | What did I try to fix my Wi-Fi dropping in the evenings? | `Wi-Fi dropping evenings`: 0 - archive/…/old-router-notes~, area-monitor~, correction-47-minute~ | read_note (archived note) | right | 28.3 s |
| 112 | What was in my pancake recipe note? (trashed) | `pancake`: 0 - sunday-chapati~ …; then `recipe`: 1 - sunday-chapati\* | read_note (chapati) | the trashed note stayed invisible; offered the chapati recipe, labelled as not pancakes | 49.6 s |
| 113 | Add to Alice Chen's note that she's moving to Nairobi in January | `Alice Chen`: 3 - alice-chen\*, area-monitor-project-index\*, team-standup\*, september-15-daily~ | read_note, write_note | found the right note; **appended the whole note again** (the known write_note problem) | 61.4 s |
| 114 | Delete the UART ring buffer snippets note | `UART ring buffer snippets`: 1 - uart-ring-buffer-snippets\*, … | rename_or_move_file(trash) | right: deleted on an exact match | 26.3 s |
| 115 | Average sleep in my gym log, and is it below 7 h? | `gym log sleep`: 1 - gym-and-life-log\*, … | read_note, calculate | right: 6.44 h, below 7 (calculate, not statistics) | 57.1 s |
