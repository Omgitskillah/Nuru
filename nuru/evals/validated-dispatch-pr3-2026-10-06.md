# PR 3: validated dispatch and normalized tool outcomes

Started from `plan-executor` at `535517b` after PR 2 merged; implementation branch
`feat/validated-tool-dispatch`. Qwen's ordinary tool loop is the primary path.
Successful turns do not gain a planning or repair model call.

## Implementation

- Connected tools' advertised JSON Schemas are compiled once per Session.
  Arguments must be an object or a JSON object string, with finite JSON values;
  unavailable tools, malformed JSON, scalar arguments and schema violations are
  rejected before dispatch. Diagnostics are bounded. Python's model schema accepts
  only `code` and `notes`; injected stdin, timeout and sandbox overrides are rejected.
  The injected request is checked against the original server schema separately.
- Ordinary calls, plan steps, forced writes, automatic first reads and Python's
  prerequisite reads use the same validated dispatch and outcome classification.
  Existing write checks remain in that boundary. Tool-specific adapters recognize
  Math/Python `ok:false` even if the protocol flag says success. JSON note content
  containing `ok:false` is not reclassified as an error. Successful payloads remain
  unchanged, preserving search provenance and executor value extraction.
- Outcomes include success, error kind/code, diagnostic hint, repairability and
  completion certainty. Model results, CLI execution logs and shadow accounting
  agree about failures. Python partial stdout/stderr stays available as diagnostics,
  with no successful computation claim. Programming errors are marked internal and
  stop execution rather than being sent for model repair.
- Ordinary recoverable failures allow one subsequent correction call, restricted to
  the failed tool (or available reads/computations if that tool is unavailable).
  Sibling calls are not dispatched after failure, and no dependent write runs while
  evidence is failed. Forced write argument repair is limited to one attempt and
  counts toward the tool-round limit. Plans can repair a referenced draft or literal
  arguments once, then stop dependents if the result still fails. Plan tool attempts,
  including repairs, count toward the existing step cap when enforced.
- Before a write, SQLite commits an `in_flight` attempt with vault scope, operation,
  arguments, argument fingerprint and turn identity. A successful receipt marks it
  completed. Any error after dispatch is conservatively uncertain: server errors can
  follow a successful filesystem change or failed index cleanup. There is no
  automatic replay of such an attempt. Any pending attempt blocks further writes to
  that vault, across turns, Sessions and reopen/crash recovery, independently of
  Laya/shadow state. The pending check and allocation use one immediate transaction.
- A later model failure preserves the user request and a truthful summary of prior
  completed/uncertain writes. Uncertain writes stop both ordinary and planned paths
  immediately. Cancellation also retains the action summary in history.
- Each suite case has its own conversation database and journal; sequence turns keep
  the same one. Scratch-vault resets cannot contaminate later cases with a pending
  attempt from an earlier fault.

## Operator reconciliation

The model cannot clear a journal entry. After stopping other sessions using the
vault and inspecting actual note contents/move destinations, the operator can use:

```text
/writes
/resolve-write ID completed
/resolve-write ID not_completed
```

These commands only record an operator's verified conclusion. They do not execute
or retry a write. Leave the entry unresolved if the outcome cannot be established;
reads remain available. The CLI is the reconciliation interface for this PR; the UI
continues to display the failure and can perform read-only inspection.

This is a conservative fallback, not server-side exactly-once execution. The servers
do not provide idempotency keys or a general operation-status receipt. Aliases are
not used to bypass the blocker: it applies to all writes in the same vault/database.
Separate database files do not share that journal. Canonical authorization/target
provenance and independent mutation scope remain PR 4. A dispatched server rejection
may therefore require manual reconciliation even when the server made no change.

## Verification before model runs

`VAULT_PATH=/tmp/ci-vault .venv/bin/python -m pytest -q --disable-warnings`:
**679 passed**. Coverage includes malformed/scalar/nonfinite/schema-invalid input,
unavailable tools, protocol/domain disagreement, nested Math diagnostics, Python
partial output and injected-field overrides, prerequisite-read exceptions,
ordinary/forced/planned uncertainty, dependent-write refusal, bounded repair,
crash/reopen and two-connection journal protection, operator reconciliation, and
model failure after confirmed writes in both execution modes.

Actual MCP transport probe:

```sh
.venv/bin/python nuru/evals/decision_layer/tool_contract_probe.py \
  --out nuru/evals/decision_layer/.runs/tool-contract-pr3-20261006.json
```

All six probes passed, without a model or vault writes:

| Tool | Input/result | Protocol error flag | Classified outcome |
| --- | --- | --- | --- |
| calculate | `2+2` | false | success, result 4 |
| calculate | prose instead of arithmetic | true | domain, UNKNOWN_IDENTIFIER |
| statistics | `[1,2,3]` | false | success |
| statistics | empty dataset | true | domain, EMPTY_DATASET |
| run_python | `print(4)` | false | success |
| run_python | print partial, then raise | true | domain, SCRIPT_ERROR; partial output retained |

These observations verify the pinned Rust servers' actual transport behavior.
Unit tests additionally cover disagreement between protocol and domain flags.

## Live validation

The focused batch ran after the clean implementation commit `1dad685`, with CPU
available and Ollama idle. Command:

```sh
MODEL=nuru-pr3-dispatch-eval NURU_BASE_MODEL=qwen3.5:4b \
NURU_PLANNER_MODEL=nuru-pr3-dispatch-eval NURU_MAX_NUM_CTX=16384 \
caffeinate -i .venv/bin/python nuru/evals/decision_layer/run_suite.py \
  --tests 9,24,26,28-31 --tiers default --label pr3-dispatch-smoke --timeout 600
```

Artifact directory: `decision_layer/.runs/suite-pr3-dispatch-smoke-20261006-113732`.
Configuration: Qwen3.5:4b Q4_K_M, thinking off, context 16,384, isolated model tag,
`nomic-embed-text-v2-moe`, Python sandbox available, no expert or live planner.
The manifest records model/server digests, case/scorer hashes and the vault snapshot.
Each case used a separate scratch vault index and conversation journal.

| Case | Score | Tool calls | Turn seconds | Observation |
| --- | --- | --- | --- | --- |
| 9 | 5/5 | 4 | 64.5 | Search/read/statistics/calculate; no Python selection |
| 24 | 5/5 | 2 | 20.5 | Read-only injection case |
| 26 | 5/5 | 2 | 19.0 | Read-only case |
| 28 | 5/5 | 1 | 10.0 | One confirmed write |
| 29 | 5/5 | 3 | 21.8 | One confirmed write after reads |
| 30 | 4/5 | 3 | 20.2 | Correction targeted the vendor-email note, not the Area Monitor project |
| 31 | 2/5 | 7 | 48.9 | Asked for clarification instead of creating today's transcript |

Total **31/35**. No timeout, crash, reported turn failure, tool failure or shadow
failure occurred. The three dispatched writes each had a completed journal receipt;
no pending/uncertain attempts remained. This batch had no failed dispatch requiring
repair, so injected-fault unit tests establish the retry/uncertainty behavior.
Successful turns added no planner or repair model calls. Case 9 chose Math tools,
so Python success/error transport evidence comes from the six MCP probes, not from
model selection in this batch.

The two content misses remain visible: normalized dispatch cannot guarantee the
model selects the right note or fulfills a requested creation. Target provenance
is further PR 4 work; this PR does not claim it is solved. These seven cases are one
sample each, not a paired performance comparison or full acceptance run. The full
paired 34-case baseline/candidate acceptance remains later hardening work. Earlier
v1.5/no-Python history is not a comparable baseline; do not infer latency or quality
improvement from these results. Raw run artifacts remain ignored; this report is
the committed record.
