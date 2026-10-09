# Stewardship implementation milestones

Implementation branch: `feat/vault-link-integrity`. User authorized the remaining
work packages on 2026-10-08, with individual commits for each milestone.

Background inference is OFF. The user authorized local Nomic v2 and Qwen 3.5 4B
for the 2026-10-09 validation work; other live runs require prior authorization.
Paid remote resources and real-vault pilots require separate approval. Development
uses temporary synthetic vaults; reports never mutate authored notes. No model
tool-list change is implied by exposing new read-only MCP tools.

| Milestone | Deliverable | Status |
|---|---|---|
| WP01.1 | Reconciled snapshots, explicit schemas, read-only property checks | Backend tested: 17 cases |
| WP03.1 | Streaming asset hashes, ownership inventory, duplicate candidates | Backend tested: 3 cases |
| WP04.1 | Fixed-clock task ledger and explicit mirror conflicts | Backend tested: 5 cases |
| WP09.1 | Declared tables and decimal arithmetic checks | Backend tested: 15 cases |
| WP06.1 | Explicit project review checklist and evidence digest | Backend tested: 2 cases |
| WP05.1 | Explicit metadata routing previews with scope boundaries | Backend tested: 2 cases |
| WP07.1 | Exact copies and bounded lexical candidate pairs | Backend tested: 3 cases |
| WP08.1 | Scoped structured claims and explicit unit comparisons | Backend tested: 3 cases |
| WP10.1 | Resolved link graph and explicit navigation indexes | Backend tested: 2 cases |
| WP11.1 | Existing index compatibility/freshness and fake-embedder validation | Backend/worker tested; Nomic v2 live index/retrieval checks PASS on two test vaults; contention/fault stress pending |
| Shared.1 | Opt-in worker, findings lifecycle, CLI/MCP, restart/reconciliation | Tested; default disabled |
| Validation.1 | Independent fixtures, CI correctness, measured offline results | Local PASS; new GitHub CI awaits publication |
| Validation.2 | New supplemental vault, isolated benchmark copy, local model checks | Deterministic and Nomic index checks PASS; Qwen advisory quality FAIL; independently labeled held-out work pending |
| Interpretation | Bounded injectable stage, approval/budget/citation checks | Fake-provider and explicit Qwen eval adapters tested; production adapter/semantic acceptance incomplete |
| Pilot | Held-out human labels and consenting real-vault review efficacy | Requires approval |

Backend code lives in the Obsidian dependency; orchestration/evals live in Nuru.
Each backend milestone is a separate dependency commit and corresponding Nuru
pin/documentation commit. Persistent finding identity is a shared later milestone;
early path-derived IDs are stable across line shifts but not renames. Cold scans
and hashing reconcile missed events; this is not a constant-time event index.

Do not interpret a first deterministic milestone as completion of a whole package.
Optional semantic quality and human efficacy need independent acceptance evidence.

See [2026-10-09 validation vaults and results](../../evals/vault_steward/validation-vaults-2026-10-09.md)
for the exact scope, oracle correction, repeat protocol and model failures.
