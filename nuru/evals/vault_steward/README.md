# Vault stewardship evaluations

The remaining packages now have deterministic first milestones. See
[implementation results and scope](steward-implementation-2026-10-08.md),
[combined independent fixture](steward_fixture.json), and
[model-free evaluator](steward_eval.py). Optional background inference remains OFF.
The user authorized a separate Qwen-only local evaluation; see
[protocol and findings](qwen-steward-2026-10-09.md). Other live runs still require
authorization. The original WP02-only results below are unchanged.

WP02 is a model-free link-audit implementation.
[Results and limitations](wp02-link-integrity-2026-10-07.md) record the first offline validation. The reproducible fixture manifest
and oracle are [wp02_fixture.json](wp02_fixture.json); the harness is
[wp02_eval.py](wp02_eval.py). Materialized vaults live in temporary directories,
with the oracle kept outside the scanned vault. Neither DummyVault nor a real vault
is used or changed by the harness.

From the Nuru checkout:

```bash
uv run --locked --package core nuru-vault-links /absolute/path/to/vault
uv run --locked --package core nuru-vault-links /absolute/path/to/vault --watch --interval 30
uv run --locked --package obsidian-mcp obsidian-mcp-links /absolute/path/to/vault --all
uv run --locked python nuru/evals/vault_steward/wp02_eval.py --sizes 1000,10000 --repeats 5 --output /tmp/wp02-results.json
```

Use the Obsidian command for explicit policy, pagination, and reverse queries.
`nuru-vault-links` provides one-shot default findings and a background watch mode.
Watch mode emits one JSON digest per changed snapshot, not one notification per
file. It polls and coalesces edits, so a missed filesystem event cannot permanently
hide a change. Default interval is 30 seconds; no-change passes rehash files but
reuse parsing. Stop with Ctrl+C. No inference service or Nuru chat session is needed.

To opt into the same model-free worker during normal Nuru sessions, set
`NURU_LINK_AUDIT=1` and optionally `NURU_LINK_AUDIT_INTERVAL=30` in `.env.nuru`.
Policy configuration (`OBSIDIAN_LINK_POLICY`) belongs in `.env.obsidian` or the
process environment so the worker and MCP server use the same policy. The worker
logs bounded summary events and exposes its latest report through
`Session.link_auditor.latest`; it starts/stops with the session. Normal Nuru startup
still loads its chat model; use the standalone watch command when that model is
unavailable. No setting was enabled in the developer's actual environment.

The MCP server registers a read-only `audit_links` tool, callable by the harness
or another MCP client. It is **not added to Nuru's default model tool allowlist**:
existing prompts/tool lists remain the baseline until separate model evaluation.
The parser, resolver, reverse index, CLI, worker lifecycle, and MCP protocol are
already testable without models. Candidate revisions and evidence can support
future ranking; automatic repair is outside this version.

## Grading and timing

The fixture oracle specifies every supported reference occurrence by source line,
status and reason, including five broken references, two ambiguities, deliberate
placeholders, excluded templates, external URIs, and valid controls. It also
expects zero extracted references from code/comment examples. The evaluator fails
on a missing/extra/misclassified occurrence or any changed source file. Broken-link
precision/recall use occurrence counts; full exact matching checks other statuses.

Scale runs generate unique medium-length notes with file, heading, block, and
missing-file references. Five fresh-index passes report cold parsing, no-change
reconciliation, one-note edits, and incoming queries **including reconciliation**.
Cold means a fresh parser cache, not a flushed OS disk cache. Raw samples are
retained. Peak RSS is the process high-water mark, not a per-scan allocation or the
worker's steady-state memory. No GPU/embedding/model timing is measured.

[Work package](../../docs/vault-steward/02-link-integrity.md) and
[shared protocol](../../docs/vault-steward/README.md) define subsequent acceptance:
held-out correctness, real-vault review effort, and interactive contention need
separate measurements. Synthetic perfection is not a production efficacy claim.
