# M5: index faults, recovery and retrieval contention

Fake inference tests exercise actual SQLite write locking from another connection,
truncated database bytes, a failure on the second embedding batch, edits during
embedding, an unrelated SQLite database, and cross-vault index reuse. Worker tests
interrupt an active thread and resume its persisted queue in a new worker; a real
write lock reaches the worker's observable error and clears after release.

Two failures were reproduced before fixing them: stale revisions were published
after mid-embedding edits, and unrelated SQLite files acquired semantic tables.
The backend now validates database schema before writes, binds new/reconciled
indexes to their canonical vault root, bounds lock waiting to one second, closes
failed connections, and rechecks note bytes before publishing vectors. A changed
note retains its old complete revision, pending work and recorded error until
successful reconciliation. Corruption fails visibly; no automatic destructive
reset occurs. Explicit rebuild in the test removes only a disposable known index.
Checks reduce concurrent-edit races; they are not filesystem-wide transactions.

Reproduction (no models):

```sh
.venv/bin/python -m pytest -q servers/obsidian/tests/test_semantic_faults.py nuru/tests/test_vault_indexer.py
```

## Repeated live index acceptance after fixes

The unchanged `nomic_index_eval.py` protocol was repeated after relevant semantic
changes: five sequences on each test vault, all ten passing; 20/20 new-vault and
25/25 benchmark expected notes ranked first. No-change passes made zero embedding
requests, incremental/rebuilt vectors matched with maximum error 0.0, and source
vault bytes stayed unchanged. Model, parser and harness hashes are in
[readiness-nomic-results-2026-10-09.json.gz](readiness-nomic-results-2026-10-09.json.gz).
Earlier acceptance is historical, not pooled with the new backend revision.

The benchmark-copy lane requires `../DummyVault` (or the prepared authorized copy).
Synthetic/fault/contention lanes do not. The two roots and indexes stay separate.

## Frozen contention workload

Five disposable 36-note vaults: four unchanged topic controls and 32 multi-chunk
archive notes, edited for the on lane. Production `VaultIndexer` runs Nomic v2
while 20 interactive production semantic searches execute per mode. The query
start barrier ensures overlap with active embedding, not merely an idle thread.
Matched model digest, 400-character chunks, batches of two, top-five queries.
Gate fixed before timing: every paired on/off p95 ratio must be <=1.10.
Nomic v2 digest `ff9c2f10ef5e3722623a1b396e1e04efc27a93112c83e9b7b7b9ca1d05620965`,
Ollama 0.35.1. No chat or paid provider was used. CPU/RSS and queue status are raw
artifacts. RSS is process peak, not incremental worker memory; CPU includes threads.

| Repeat | Off p50 / p95 ms | On p50 / p95 ms | p95 ratio |
|---|---|---|---|
| 1 | 37.28 / 40.07 | 47.71 / 54.53 | 1.361 |
| 2 | 37.37 / 39.79 | 48.02 / 56.02 | 1.408 |
| 3 | 37.94 / 39.94 | 48.59 / 53.08 | 1.329 |
| 4 | 37.83 / 40.03 | 47.61 / 55.20 | 1.379 |
| 5 | 37.08 / 40.04 | 47.69 / 51.96 | 1.298 |

**Gate FAIL, five of five.** Median ratio 1.361; range 1.298–1.408. All 200 queries
found the expected note in the top five. Worker stop/recovery and no-change
zero-call checks passed; topic bytes stayed unchanged. Cancellation was honored
between notes and the remaining queue reconciled after restart.

No automatic overlapping-index rollout is justified by this workload. Keep
maintenance explicitly scheduled outside interactive use or pause the existing
indexer before an interactive retrieval workload. Priority coordination and a
balanced/randomized workload are follow-up acceptance work. The stewardship
worker remains opt-in/model-free and never initiates index embedding itself.
The existing semantic indexer's scheduling defaults are not changed here.

Off always ran before on, so warm/cache/order bias remains. This measures the
retrieval stage on small synthetic inputs, not whole-turn Qwen latency, larger
personal vaults or user experience. The absolute increase is modest but still
fails the declared gate. Do not generalize the ratio to all workloads.

```sh
.venv/bin/python nuru/evals/vault_steward/readiness_contention_eval.py --repeats 5 --output /tmp/readiness-contention.json
```

Raw results: [readiness-contention-results-2026-10-09.json.gz](readiness-contention-results-2026-10-09.json.gz).
