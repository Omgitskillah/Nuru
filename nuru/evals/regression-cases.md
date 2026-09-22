# Regression cases

Cases pulled from real sessions, kept because each one pinned down a specific defect. They are
not part of the 34-prompt scored suite (`nuru-test-results-2026-09-21.md`) - re-run them when the
thing they exercise changes, and compare against the transcript recorded here rather than a score.

---

## 1. Silent turn mid-plan: "Who do I know in Berlin?"

**Prompt**

```
From my notes, Who do I know in Berlin?
```

**Vault:** `dummyVault` (read-only for this case - nothing here mutates it).
Relevant notes: `nuru/People/alice-chen.md`, `nuru/priya-raman.md`, `nuru/People/bob-martinez.md`.

**Ground truth**

| Note | Location | In Berlin? |
|---|---|---|
| `alice-chen.md` | `## Location` → Berlin, Germany | yes, stated outright |
| `priya-raman.md` | "Timezone: same as me, Berlin" | yes, but only inferable from the timezone line |
| `bob-martinez.md` | `## Location` → Munich, Germany | **no** - has to be ruled out |

A complete answer names Alice and Priya and excludes Bob. Priya needs an inference (there is no
Location field) and Bob needs an exclusion, so partial credit is easy to spot.

**What happened on 2026-09-22, before the thinking-off retry existed**

Log `logs/20260922-210604_e337775f.jsonl`, conversation 145 in `db/conversation.db`.
Model `nuru` on qwen35, 9.7B Q4_K_M. Four turns: the question, then three hand-typed `continue`s.

| Turn | Input | Tool call | Reply | Completion tokens |
|---|---|---|---|---|
| t-1 | the question | `search_notes("Berlin")` | silent | 98 |
| t-2 | `continue` | `read_note("priya-raman")` | silent | 87 |
| t-3 | `continue` | `notes_with_tag("person")` | silent | 83 |
| t-4 | `continue` | `read_note("bob-martinez.md")` | **the full correct answer** | 180 |

Every silent call ended on a stated intention, which the model treated as the act itself:

- t-1: "… Let me read these notes to confirm who you actually know."
- t-2: "… Let me answer the user's question."
- t-3: "… Let me also read bob-martinez.md to check if Bob Martinez is in Berlin."

All three ended `done_reason=stop` at under 100 completion tokens - a routing failure, not a
truncation. Because a silent turn stores its reasoning as the reply (`session.py`), the DB kept
all of it, which is why this case is reconstructible at all.

Two things worth knowing about the `continue`s:

- **They only advanced the plan one step per turn.** The model was walking a list of person notes
  and answered on the turn after it read the last one. `continue` pumped a finite queue; it did
  not repair the routing.
- **One of them went backwards.** t-2 already held the complete answer and resolved to deliver it
  ("Let me answer the user's question"). The next `continue` regressed to retrieval instead
  ("Now I have three person notes. Let me read each one"), costing a turn.

The turn that worked is also the only one where the model wrote to the content channel at all:
t-4's *first* call emitted narration *and* a tool call ("I need to finish checking the other
person notes. Let me read bo…", 273 tokens). That call shape occurs 5 times in 674 logged model
calls, and it is what distinguishes t-4 from t-1..t-3 - not the word "continue".

**What to check now**

The thinking-off retry should fire at the end of t-1, where the model said "Let me read these
notes" and stopped, and collapse the whole exchange into one turn. Expect:

1. `[retry] No reply was written - asking again with thinking off.` in the CLI, or
   "retried with thinking off" on the reply header in the UI. If that line never appears, the
   retry did not trigger and nothing below matters.
2. **A reply on turn one, with no `continue` typed.** This is the bar. Anything else is a fail.
3. Then, how complete it is. The retry inherits only what t-1 had - the search results, not the
   three notes t-2..t-4 went on to read - so either outcome is informative:
   - it answers from the search snippets: check whether Bob is still correctly excluded, since
     that only came from reading his note;
   - it calls `read_note` itself and the loop finishes the plan with thinking off: compare the
     answer against t-4's, which is the reference.

A one-turn answer that is thinner than t-4's is still a pass on delivery and a finding about the
trade - note which of the three ground-truth rows it got, rather than scoring it 0-5.
