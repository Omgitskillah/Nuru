# WP02 deterministic link integrity — results (2026-10-07)

Implemented the read-only Obsidian auditor and reverse index, bounded MCP tool,
standalone CLI, and Nuru's opt-in polling worker on `feat/vault-link-integrity`.
No real vault or DummyVault was changed; no model, embeddings, paid API, or external
URL check was run. Background operation remains off unless explicitly enabled.

## Correctness

The curated synthetic fixture matches **all 23 reference occurrences** exactly:
13 resolved, 5 broken, 2 ambiguous, 1 deliberate unwritten destination, 1 excluded
reference, and 1 external URI. All five seeded defects were found with zero false
broken flags: precision/recall 1.0 on this small fixture. Authored file hashes were
unchanged. This is reproducible fixture correctness, not a held-out or real-vault
accuracy estimate.

The combined offline suite passed **1,301 tests**, with one existing live-embedding
test skipped (requires Ollama/nomic v1.5). An independent standalone dependency
installation passed **560 tests**, with the same skip. The installed Obsidian CLI
also returned the curated classifications correctly. The two suite counts overlap;
they are not 1,861 distinct tests.

New coverage includes root/relative/shortest Markdown paths and collisions,
Unicode normalization, aliases as display text, code/comments, reference-style
links, headings/blocks, duplicates, assets, explicit placeholders, exclusions,
revisions, pagination, reverse queries, no-change parsing reuse, edits/renames/
deletions/recreation/restart, 100 coalesced edits, policy reload, worker lifecycle,
unreadable paths, symlink swaps, incomplete catalogs, mid-scan edits, and MCP calls.
Network creation is blocked in the new feature tests; model services are not
needed for any new case.

## Initial timing

Apple M4, 16 GiB RAM; macOS 26.6.2 arm64; Python 3.14.7 and markdown-it-py 4.2.0.
Five repeats at each scale. All columns are median milliseconds of Python
`LinkIndex.report`, including reconciliation and snapshot hashing. Incoming queries
also reconcile first; these are not constant-time lookup-only measurements. Cold
means a fresh parsing cache with normal OS caching, not a cold disk or reboot.

| Notes | Cold parsing | No-change pass | One-note edit | Incoming query including scan |
|---|---:|---:|---:|---:|
| 1000 | 788.0 | 115.1 | 111.0 | 116.6 |
| 10000 | 7663.4 | 1147.5 | 1113.4 | 1009.6 |

After each one-note edit, **only one note was reparsed** in every repeat. No-change
passes generated zero model/embedding calls. They still read/hash all note files
and rebuild resolution/reverse edges. At 10K notes, reconciliation is roughly one
second rather than the initial several-second parsing pass. Polling defaults to
30 seconds, so detection delay also includes the remaining polling interval.

Process peak RSS was approximately 201.6 MiB across
this evaluator; it is a high-water mark, not incremental worker allocation. The
machine was not isolated from all background work. Raw ranges/samples are retained;
these measurements do not establish an improvement over an existing equivalent
full auditor or satisfy the interactive-contention gate.

## Reproduce

```bash
VAULT_PATH=/tmp/wp02-placeholder-vault OBSIDIAN_SEMANTIC_DB='' uv run --locked pytest nuru/tests servers/obsidian/tests servers/obsidian/vendor/access-logging/tests servers/deep-think/tests servers/laya/tests -q --disable-warnings -rs
uv run --locked python nuru/evals/vault_steward/wp02_eval.py --sizes 1000,10000 --repeats 5 --output /tmp/wp02-results.json
uv run --locked --package core nuru-vault-links /path/to/vault --watch --interval 30
```

The evaluator always creates its own temporary vault; the last command is an
explicit user-selected, read-only vault audit. [Usage](README.md),
[fixture/oracle](wp02_fixture.json), and [raw results](wp02-results-2026-10-07.json.gz).
The raw artifact records exact source hashes and dependency commit
`96c7d62d36613a778be2923c6543d1bc1c226b92`. Nuru's base is the work-package branch at
`6126307d997712aadd54e3aff5fd369c1c3c417e`; the accompanying implementation commit contains the
worker, evaluator and dependency pin.

## Follow-ups and acceptance not yet measured

- Model-assisted repair ranking and model tool-selection behavior: **NOT RUN**;
  model unavailable. `audit_links` is harness-callable, not on Nuru's default model
  tool allowlist. Existing model prompts are unchanged.
- Held-out semantic grading and manual/assisted real-vault review time: **NOT RUN**.
- Worker-on interactive p95 contention and CPU-time samples: **NOT RUN**. The
  synthetic resource measurements are an initial engineering baseline only.
- Frontmatter/HTML/plugin/Canvas links, attachment fragments, renderer-specific
  slug variations, persistent identity/acknowledgement state, and automatic repairs
  remain outside v1. Coverage gaps surface as diagnostics/unsupported results.
- Source renames change finding IDs; normal reruns and line shifts retain them.
  All writes to authored notes remain out of scope. No watcher or setting was
  enabled for the actual benchmark or personal vault.
