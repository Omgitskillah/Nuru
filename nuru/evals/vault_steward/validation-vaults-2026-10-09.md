# Validation vaults and model acceptance — 2026-10-09

The user authorized creation of a new vault, a copy of `../DummyVault` in this
checkout, and local inference with Nomic v2 plus Qwen 3.5 4B. Background inference
remains disabled; no source-vault writes, real personal-vault pilot, paid API,
model-definition change, push, or GitHub CI run is implied by this authorization.

## Vaults

- `VaultStewardValidation/`: 35 new Markdown notes and two identical PNG assets.
  The first-use examples cover explicit schemas, JSON/YAML metadata, broken links
  versus code examples, task mirrors/history/projects, valid and invalid totals,
  active/paused/completed projects, protected inbox captures, exact copies,
  equivalent/conflicting/conditional claims, navigation links, Unicode names and
  excluded templates/generated reports.
- `DummyVaultValidation/`: 51 authored files copied from `../DummyVault` without
  modification. Hidden runtime/configuration files, SQLite/database files,
  logs and symlinks are excluded. The copied content and original source hashes
  were checked during preparation. Excluded configuration means this copy does
  not reproduce every plugin setting or source index.
- `.validation-indexes/`: dedicated local persistent indexes, outside either
  authored vault. These are separate from the interactive index and suite indexes.
  Configuration is explicit; no environment file is changed.

All three directories are ignored by Git. The tracked
[fixture generator and oracle](validation_fixture.py),
[copy/materialization script](prepare_validation_vaults.py), and
[manifest](validation-vault-manifest-2026-10-09.json) provide reproducibility.
Oracles and results remain outside the vaults and are not sent to either model.
The preparation command refuses to overwrite existing validation vaults.

This is a supplemental first-use fixture, not independently human-labeled
held-out acceptance. It is smaller than the proposed 100-note development and
50-positive/50-negative semantic sets. No thresholds or prompts were tuned on
its measured model responses.

## Deterministic scan

[Results](validation-scan-results-2026-10-09.json): 16 expected finding/review
occurrences matched exactly, with zero missing or extra occurrences. Exact-copy
groups, asset hashes/duplicate bytes, graph edges, scoped claim classifications,
the 17.50 EUR expected total, protected triage, no-change reconciliation and
unchanged source bytes pass. Finding matching is not a defect-only accuracy
metric: route proposals and explicit abstentions are included.

The initial oracle omitted two intentionally expected `triage.abstain` report
rows for novel/protected captures. Oracle revision 2 adds those rows; the fixture
and backend were not changed. The initial mismatch is an oracle omission, not
evidence of a product fix or a blind held-out pass.

The benchmark-copy coverage scan processes 40 non-template notes and reports
19 broken-link occurrences. It is UNLABELED: these are not counted as 19 confirmed
true defects. Tables, project reviews and routing need suitable explicit schemas
and metadata, so they were not silently inferred for this coverage scan.

## Nomic v2 live index validation

[Harness](nomic_index_eval.py) and
[five-repeat results](nomic-validation-results-2026-10-09.json) use the production
Ollama embedder and semantic index backend on temporary isolated copies. Paths
are resolved before scanning, fixing the earlier invalid macOS harness setup.
The live model is `nomic-embed-text-v2-moe:latest`, digest
`ff9c2f10ef5e3722623a1b396e1e04efc27a93112c83e9b7b7b9ca1d05620965`.
Chunks are 400 characters, batches at most two, truncation off; vectors have 768
dimensions. Digest is checked before every embedding request.

All ten repeated index sequences PASS: exact eligible-path coverage, no-change
zero calls, same-mtime one-note edit, same-title relocation with vector reuse,
deletion with zero embedding calls, incremental/full-rebuild equivalence,
pause without inference, persisted queue restart, incompatible digest rejection
and batch bounds. Maximum rebuild vector difference is **0.0**.

| Vault | Queries × repeats | Expected note at rank 1 | Cold index median | No-change median |
|---|---:|---:|---:|---:|
| New fixture | 4 × 5 | 20/20 | 1,235.551 ms | 15.553 ms |
| Benchmark copy | 5 × 5 | 25/25 | 6,150.000 ms | 19.458 ms |

The benchmark queries target the correction, PSM debugging, FreeRTOS, Berlin
and revised battery notes. Query ranks are **chunk ranks** in the top ten results,
using the first chunk from an expected note. These nine explicit queries are
small retrieval controls, not the original 32-test conversational benchmark.
Repeats use fresh databases but warm model/OS caches. They are not independent
query samples. The main experiment made 1,615 embedding requests; the separate
one-repeat smoke is not pooled with these results. Embedding token counts are
NOT COLLECTED by the production wrapper. No interactive contention, corruption,
concurrent-lock stress or embedding-scale quality gate is claimed.

## Qwen advisory validation

[Harness and question/decision/rubric manifest](qwen_vault_eval.py) use ten new
fixture cases plus two benchmark notes. Model: `qwen3.5:4b`, pinned digest;
16K context, temperature zero, 512 output tokens, JSON, explicit raw ChatML with
thinking closed. Complete prompt UTF-8 bytes plus two provide a conservative
token reservation; measured usage must fit. Only candidate questions and source
excerpts are sent, not the expected decisions or grading rubrics.

Generation is advisory only. Verbatim citation checking does not validate the
meaning of a suggestion. The smoke exposed wrong task completion, wrong table
arithmetic, a suggested target drawn from a code example, and two invalid
citations. Preserve these as failures; no prompt changes are made between the
smoke and repeated run. Human semantic labels and production efficacy remain
pending.

[Five-repeat raw results](qwen-vault-results-2026-10-09.json): **60 calls**,
**50/60** pass format/citation checking, **45/60** match the expected decision
category. Median generation-request wall time is **3,853.162 ms**, maximum
**7,478.151 ms**. Measured input/output tokens: **26,740 / 7,405**, with zero
budget overruns or retries. All source bytes remain unchanged. Each of the 12
cases returned identical output across five temperature-zero repeats.

Assistant inspection identifies the following failures, each repeating five times:

| Case | Failure | Boundary outcome |
|---|---|---|
| Links | Suggests checking a replacement drawn from a fenced code example | Accepted cautious decision and real quote; irrelevant advice still passes |
| Tasks | Says the mirrored task is completed, choosing the checked copy despite the open copy and absent authority | Accepted real quote; wrong conclusion passes |
| Tables | Repeats 20.00 EUR instead of calculating 12.25 + 5.25 = 17.50 | Accepted real quote and expected decision category; wrong arithmetic passes |
| Unit-equivalent claims | Rewrites JSON into a shortened quote not present verbatim | Rejected citation |
| Graph | Joins separate link lines into an inexact quote and describes an unsupported direct edge | Rejected citation |

The attachment answer stays cautious but asks about other references instead of
clearly acknowledging the already supplied embed; treat its usefulness as partial.
The correction and battery benchmark cases pass citation and decision checks in
all ten repetitions. The correction answer gives the network NAT explanation and
600-second keepalive. The battery answer stays uncertain, preserves the failing
always-connected architecture and cites the six-reports/hour sensitivity; it
does not claim an unconditional three-year pass. These two supplied-note answers
do not test retrieval, reasoning across notes, or autonomous tool use.

**Advisory semantic acceptance: FAIL.** Valid quotations establish provenance, not
task authority, arithmetic correctness, or relevance. Deterministic findings
should remain authoritative for these supported checks. A subsequent candidate
preparation/interpretation milestone can preserve parsed code boundaries, supply
both conflicting occurrences and calculated totals, and grade quality on new
cases; this experiment does not implement or establish that improvement.

## Persistent local indexes

[Bootstrap results](validation-index-bootstrap-2026-10-09.json) record dedicated
indexes for 33 eligible new-fixture notes and 40 eligible benchmark notes; the
excluded fixture template/report and benchmark templates are not embedded.
Construction made 33 and 123 requests respectively, separate from the repeated
experiment. Both no-change reconciliations made zero calls and both vaults stayed
byte-identical. The explicit environment mappings are also saved locally in
`.validation-indexes/configuration.json`; no live `.env` was edited. These indexes
can support later tests without rebuilding the source vault's shared index.

## Reproduction

From a fresh checkout root with no existing validation directories:

```bash
.venv/bin/python nuru/evals/vault_steward/prepare_validation_vaults.py --source ../DummyVault --output /tmp/validation-manifest.json
.venv/bin/python nuru/evals/vault_steward/validation_eval.py --output /tmp/validation-scan.json
.venv/bin/python nuru/evals/vault_steward/nomic_index_eval.py --repeats 5 --output /tmp/nomic-validation.json
.venv/bin/python nuru/evals/vault_steward/qwen_vault_eval.py --repeats 5 --output /tmp/qwen-validation.json
.venv/bin/python nuru/evals/vault_steward/build_validation_indexes.py --output /tmp/validation-index-bootstrap.json
```

The model commands perform local inference; approval applies to this session's
validation work, not an automatic CI or background inference job. Persistent
index construction refuses existing databases. Offline regression suite:
**1,382 passed, 1 skipped in 12.38 seconds**, with isolated vault/index settings
and the background steward disabled. The skipped historical live embedding test
was not enabled; Nomic v2 is exercised by the explicit harness above.
