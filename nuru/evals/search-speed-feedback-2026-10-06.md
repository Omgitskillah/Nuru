# Search speed: feedback on bounded note expansion, and rank-weighted snippets (2026-10-06)

Two ideas for making Nuru answer faster after a search:

1. **Bounded note expansion**: search also returns the full text of its top hits, so the model needs
   fewer "read this note" round trips.
2. **Rank-weighted snippets**: higher-ranked matches get longer passages and lower-ranked ones shorter.

The short version: both can help, but on this machine the cost of *reading prompt text* is what
dominates, not the number of round trips. Any text the model didn't need costs more time than a skipped
call saves. Measure where the notes the model reads actually rank before building either.

## What the time goes on

Fitted from the 70 runs in PR #5's paired comparison (Qwen3.5 4B, thinking off, 16k context; 339
model calls; the split explains 95% of call time):

| Part of a model call | Cost |
|---|---|
| Fixed cost per call | 0.6 s |
| Reading new prompt text | ~5.8 ms per token (only ~173 tokens/s) |
| Writing output | ~53 ms per token (~19 tokens/s) |
| Nuru's own work between calls | ~0.7 s |

- Ollama caches text it has already read, so each call only reads what's new: typically ~760 tokens
  (~4.4 s); 90% of calls read under ~1,400.
- One `search` result adds ~1,100 tokens: up to 10 matches, exact ones with a ~120-character snippet,
  meaning ones with up to 400 characters of passage.
- Calls whose only request is `read_note` are 21% of model time (20 of 96 minutes), and appear in 68 of
  70 turns, usually once.

## When the model sees the snippets

Straight away. `search` returns its whole result as the tool's reply, and the model reads it on its next
call. Nothing filters or reorders it first: Laya's `tool_gate` (Nuru running the first search) and `rerank`
(reordering matches) are both in shadow mode, so they only log. The results stay in the conversation for
the rest of the turn (cached, so not re-read), and PR #3's context budget drops the largest tool results
first when the window is nearly full.

## Feedback on bounded note expansion

**What a skipped read call saves: about 3 s, not 8 s.** A typical read-only call takes ~8 s, but most
of that is reading the search results, which the next call would have to read anyway. Skipping the call
saves its fixed cost (0.6 s), writing the ~30-token request (~1.7 s) and Nuru's work between calls
(~0.7 s). Across a typical 45 s turn that's roughly **5-7%**, more on turns that read several notes.

**What a wrong note costs: break-even is about 500 wasted tokens per skipped call.** Every expanded
token the model didn't need costs ~5.8 ms. The proposed 4,000-token retrieval cap, filled with notes the
model wouldn't have read, would cost ~23 s per turn, more than the whole saving. It would also use a
quarter of a 16k window, so PR #3's trimming starts earlier.

**The test vault flatters it.** Notes in dummyVault are small (median ~200 tokens, largest ~700), so
expanding the top one or two is cheap there. The real vault's notes are likely larger; results from
dummyVault won't transfer directly.

**On the four open decisions:**

1. *Automatic, opt-in or task-specific?* Expand only when the top hit clearly leads the rest, not on
   every search. Keep it behind a setting so it can be measured on and off.
2. *Starting limits?* At most 2 notes and ~1,500 tokens, not 4,000.
3. *Oversized notes?* Skip them and keep the snippet; the model can still read the note itself. Expanding
   only the relevant sections can come later.
4. *Who orchestrates?* Agreed: Nuru decides, because it knows the request budget, and Obsidian provides a
   bulk read that returns each note's revision.

## Rank-weighted snippets

Giving more text to higher-ranked matches sits between today's equal snippets and full expansion, and
keeps most of the gain with less risk.

**Why it could help.**
- If the top hit got ~800 characters, most dummyVault notes would arrive whole, so no read call is needed
  when the top hit is the right one.
- Ranks 4-10 are rarely read; a title and ~150 characters is enough to judge whether to open one.
- Example taper: 800 / 400 / 400 / 150 for each of ranks 4-10 = ~2,650 characters, against up to 4,000
  today. About a third fewer tokens per search (~2 s), plus the skipped read when the top hit is right.

**What to watch.**
- **The needed note isn't always near the top.** Earlier analysis found it ranked low on some tests (the
  correction note on test 2, the Berlin note on 17, the FreeRTOS note on 22). Lower ranks must keep enough
  to be recognised (title, type and the line that matched), or the model may never open them: an accuracy
  loss, not just a speed one.
- **Weight by score gap, not just position.** Meaning scores are often close together; when ranks 1-4
  score about the same, doubling rank 1's text is arbitrary. Give extra text only when the top hit clearly
  leads.
- **Taper only the meaning matches.** Exact matches are ordered by whether the words appear in the
  filename or title, not by relevance, and their snippets are already short (120 characters).

## How to decide

1. **Rank check (no model; embeddings only).** Replay the logged search queries and record where each note
   the model went on to read was ranked, and whether the passage it was shown already held the fact it
   needed. This answers both ideas: if the notes it reads are nearly always in the top 2, expansion pays
   off; if they're in the top 3-5, taper or cut after that.
2. **Token count (no model).** Re-render the same searches with tapered lengths, or with the top hits
   expanded, and count the tokens saved or added.
3. **GPU comparison.** Current against the chosen variant on the search-heavy tests (2, 17, 20, 22, 25,
   32) × 5, scoring both points and time.

## A bigger lever this turned up

Reading prompt text at ~173 tokens/s is the real bottleneck. Two cheaper options follow:

- **Smaller search results:** fewer matches (say 5 instead of 10 when the top ones are strong) or tapered
  passages speed up every turn that searches, without the risk of adding wrong notes.
- **Ollama settings:** it's not known whether ~173 tokens/s is normal for this model on this Mac. A quick
  benchmark of settings such as flash attention and batch size could show whether there's an easy gain.
