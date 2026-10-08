# Proposal: faster search evidence with bounded prompt size

**Status: for discussion; not implemented.**

## Why

Search returns snippets or passages, followed by explicit reads when needed.
Two candidates could improve latency: selectively expanding top hits into full
notes, and allocating longer passages to stronger meaning matches.

The supplied 2026-10-06 feedback fits 339 model calls from PR #5's 70-run
comparison: about 5.8 ms per new prompt token, 53 ms per output token, 0.6 s fixed
per call and 0.7 s between calls. On that analysis, skipping a typical read request
saves roughly 3 s; the necessary evidence still has to be processed. Around 500
unneeded extra tokens would consume that saving. These are reported estimates for
this machine and configuration, not independently verified here or universal rates.

The priority is therefore to measure which results are needed before adding text.
Available context capacity is not a target to fill.

## Candidates to measure

**Rank-weighted snippets:** taper meaning passages, keeping title, type, path and
enough matching text to recognise lower-ranked notes. An illustrative allocation
is 800 / 400 / 400 / 150 characters for semantic ranks 1 / 2 / 3 / 4–10. Allocate
extra text by score separation, not position alone; close scores should get similar
space. Keep exact-match snippets unchanged. Measure actual serialized token totals,
including metadata, rather than treating character savings as token savings.

**Selective note expansion:** behind a setting, expand only when the leading hit
is sufficiently distinct. Start with at most two notes and about 1,500 tokens,
including metadata. Skip oversized notes and retain their snippets; relevant-section
expansion can follow later. Nuru owns selection and request budgeting; Obsidian
provides bulk reads through existing access rules, with per-note revisions and
errors. Keep note entries distinct, label omissions, and allow further explicit
reads. Retrieved text remains evidence, not instructions.

Background prefetch and parallel file reads are secondary: neither saves prompt
processing. The small dummyVault notes may overstate expansion's value in larger
vaults.

## Budget before the model call

Reuse `core/context_budget.py` to estimate remaining capacity in the actual
request, including system text, history, the question, tool schemas, search
results and message framing.

```text
note budget = max(0, min(retrieval cap,
                        available request tokens − additional safety margin))
```

Nuru currently reserves 20% of the context window for output, capped at 2,048
tokens, and estimates text tokens as `ceil(UTF-8 bytes / 4)`. This is a heuristic,
not a tokenizer guarantee.

For a 16,384-token window, a 2,048-token output reserve, an estimated 6,000-token
existing request and a proposed 1,000-token safety margin leave 7,336 tokens.
A proposed 1,500-token retrieval cap would still limit expansion to 1,500 tokens.
The cap and safety margin are starting values to evaluate, not measured optima.

Count paths, revisions, content and bundle formatting; check the assembled request
again before sending. Preserve bounded overflow handling when the estimate is wrong.

## Evaluation and discussion

1. **Rank and evidence audit:** use logged searches and subsequent reads to measure
   needed-note rank and whether the original passage already contained the needed
   fact. Use saved results first; any replay needs the frozen vault/index to avoid
   drift. This needs no generation calls; embedding queries may use model resources.
2. **Offline token comparison:** render both candidates on the same results and
   count added/saved tokens. Distinguish semantic rank from overall result position.
3. **Model comparison after selection:** current behavior versus the chosen variant
   on search-heavy tests 2, 17, 20, 22, 25 and 32 × 5 per variant. Measure latency,
   calls, new prompt tokens, points and missed evidence. Include large notes and
   ambiguous/low-ranked hits; no model runs are part of this proposal update.

Separately benchmark prompt-processing settings if needed. Flash attention and
batch size are hypotheses to test, not established fixes for the reported rate.

Decisions to discuss:

1. What score separation predicts a useful expansion on the logged queries?
2. Does tapering preserve recognition of necessary lower-ranked notes?
3. Which candidate improves latency without reducing answer accuracy, including
   on representative larger notes?
