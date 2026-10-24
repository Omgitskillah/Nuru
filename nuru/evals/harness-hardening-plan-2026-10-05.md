# Harness hardening: assessment and implementation plan

> **Historical record.** Plan mode was removed on 2026-10-24 (PR #37). The planner, executor, planner Modelfiles and `planner_check/` files this page links to no longer exist on main; they are at commit [7c2c345](https://github.com/Omgitskillah/Nuru/tree/7c2c3458657409a6be66fa88036c09f01cd7a500).

Reviewed on 2026-10-05 against `plan-executor` at
`30e3f9e7754dc6934858ff8d4a02fae72ed38ab6`.
Review branch: `review/harness-hardening`, created directly from that commit.
Input: `/Users/aluchoayisi/Downloads/Nuru Harness Hardening.md`.

Implementation update: the review branch was rebased onto `plan-executor` at
`80171de`. That revision includes the drafting/create-versus-append changes,
step-cap final answer, eval manifests, stricter scorer, and traps document called
out as prerequisites below. The assessment remains a snapshot of `30e3f9e`.
PR1 now focuses on Qwen3.5 and Granite4.2, with explicit unsupported entries for
the remaining legacy families.

## Current implementation status (2026-10-06)

PR 1 landed on `plan-executor` through GitHub PR #2 (`b532930`). PR 2 is
implemented on `feat/history-context-budgets`: separate content estimates, atomic
legacy migration, complete-exchange selection, per-call context budgets, and
single-session sequence evaluation. See [the PR 2 validation report](history-context-pr2-2026-10-06.md)
for measured results and remaining limitations. PR 2 has merged through GitHub PR #3.
PR 3 is implemented on `feat/validated-tool-dispatch`; see
[its validation report](validated-dispatch-pr3-2026-10-06.md) for normalized dispatch,
bounded repair and durable write-attempt handling. The findings below describe the
original review snapshot; the manifest and traps document have since landed.

## Recommendation

Proceed with all three objectives. The document identifies concrete defects, but its
acceptance criteria need amendments around target identity, mutation authorization,
context estimation, and the plan executor. Split the work into four PRs: shared
prompts/profiles, history/context, tool failure handling, and mutation policy. Add
eval support with the relevant PR rather than treating the documented runner as
already complete. Allow approximately 4–6 developer days plus live evaluation time;
this is a planning estimate, not a measured delivery commitment.

The original assessment did not implement the proposal. Later user requests authorized
PR 1 and PR 2 implementation; a merge remains a separate action.

## What the checkout confirms

| Proposal | Finding | Consequence |
| --- | --- | --- |
| Share ordinary model prompts | Qwen3.5 and Granite SYSTEM bodies are byte-identical. All five other families share an older body mentioning all seven retired names listed in the document. | Extraction can preserve the current pair exactly while fixing the other five. |
| Explicit profiles | `profiles.yaml` contains only Qwen3.5 and Granite. `model_profile.for_model()` silently returns a default profile for anything else. | Strict support checks are justified, but must handle the derived `nuru` alias. |
| Separate message size from usage | `ask()` stores whole-request prompt usage on the user row; `_history_cutoff()` sums it as message size. | The history bug is real and independently reproduced. |
| Include fixed context overhead | The initial input check and history selection reserve 20% for output but omit the baked SYSTEM prompt, schemas, timestamp, and framing. | Fix both admission and history selection using the same budget calculation. |
| Normalize tool failures | `ask()` discards `_call_tool()`'s success flag, parses string arguments without recovery, and `_call_tool()` re-raises exceptions. | Failures need a common result contract and bounded recovery. |
| Enforce provenance and write intent | `DecisionShadow` keeps IDs/titles, not authorization or match provenance. `_write_blocked()` depends on a decision-layer response and configuration. | Safety-critical policy needs independent state and must run with Laya unavailable or disabled. |
| Eval tooling is ready | The runner supports one prompt per process. It does not emit the described manifest. `manifest.py` and `nuru/evals/dummyvault-traps.md` are absent. | Reproducibility and new multi-turn cases require implementation, not just commands. |

Relevant source locations:

- [Prompt/profile initialization and derived planner alias](../src/core/session.py#L57),
  [tool allowlist](../src/core/session.py#L162),
  [profile fallback](../src/core/model_profile.py#L48).
- [History cutoff](../src/core/conversation_store.py#L84),
  [input admission and history assembly](../src/core/session.py#L514),
  [usage stored on the user row](../src/core/session.py#L655).
- [Normal tool dispatch](../src/core/session.py#L721),
  [write blocking and tool execution](../src/core/session.py#L779),
  [forced writes](../src/core/session.py#L840),
  [plan execution](../src/core/session.py#L990).
- [Shadow state](../src/core/decision_shadow.py#L115),
  [executor domain-error handling](../src/core/executor.py#L70),
  [suite runner](decision_layer/run_suite.py),
  [driver](decision_layer/drive_turn.py),
  [scorer](decision_layer/score_runs.py).

GitHub-style line anchors above identify the reviewed revision; line numbers will
move during implementation.

## Pushbacks and decisions to settle

### 1. A lexical match is evidence, not authorization

Obsidian's `search` labels a note `exact` when every query word occurs somewhere in
its filename, title, or content. That is not an exact identity match. Similarly,
`find_in_notes` can match a broad model-generated regex. Neither result alone proves
the user asked to delete that particular note.

Track the user's requested operation and target constraint alongside retrieval
provenance. Bind a singular destructive request to one verified canonical target;
multiple plausible notes require clarification. A plural request may legitimately
cover several targets, but each must satisfy the user-derived predicate. Do not
treat a model's broad query, a note mentioning another note, or a later `read_note`
call as permission to mutate it. A semantic candidate can become eligible only
after independent identity/constraint verification, not merely because it was read.

Mutation intent in arbitrary language is not something a keyword regex can prove.
Use a turn-scoped operation/target authorization record at the user boundary, with
clarification or confirmation when its scope is uncertain. An LLM interpretation
can propose that record; it must not enlarge authorization after reading notes.
Explicit commands or UI confirmation provide a deterministic path. Clear existing
write requests should keep their ordinary flow; uncertain cases should stop before
dispatch. The implementation must document which natural-language forms it accepts
and test both false permissions and false refusals.

### 2. Always-on checks must cover every execution route

`run_plan()` invokes tools separately from `ask()`. Forced writes and the enforced
first search also have separate direct dispatch paths. Nuru's Python adapter reads
notes before its main tool-call `try` block. Fixing just the line named in the
document leaves gaps.

Use one model-facing dispatch boundary for availability checks, argument validation,
mutation policy, result classification, and logging. Route normal calls, resolved
plan calls, forced writes, and automatic first reads through it. Apply the relevant
read/error accounting to harness-injected Python note reads. Keep decision-layer
RPCs and expert routing as separate harness operations; this does not require a
general framework for every internal call.

The executor already recognizes math `ok:false`; it is not wholly unprotected.
However, its domain check does not recognize a Python `ok:false` payload received
with a successful protocol flag. Current Rust Math and Python handlers return
`Err(Json(...))` on failure, so verify actual MCP transport behavior with contract
tests instead of assuming every domain failure arrives with `is_error=false`.

There is another retry hazard: after a model request fails, `ask()` deletes the
current user row and suggests trying again even if an earlier tool already wrote
successfully. Preserve the request and completed/uncertain actions in that case,
and finalize a truthful partial outcome. A fresh user retry must not be presented
as though the earlier turn never had side effects.

### 3. Preserve prompt baselines, but acknowledge behavior conflicts

The current shared candidate lacks the drafting-directive rule called out as work
in progress. Its writing rules also tell Nuru to correct a wrong note proactively,
which conflicts with the proposed prohibition on unrequested writes. Extraction
alone will preserve both omissions and contradictions.

Freeze the chosen post-coordination baseline and extract it without wording changes.
Land drafting/create-versus-append wording and mutation-policy alignment as explicit
behavior changes, with new baseline hashes. The proposed final no-tools step-limit
answer is not present in the reviewed `ask()` cap branch. Rebase the dispatch/policy
work after it lands, or include it as a separately reviewed prerequisite.

Keep planner Modelfiles and executor pick/draft/answer prompts distinct: they have
different jobs. “All seven families” refers to the ordinary assistant prompt.

### 4. Strict profiles and tool validation have compatibility traps

`PLANNER_MODEL` defaults to the custom tag `nuru`; strict `for_model("nuru")` would
break startup unless that alias inherits the base model's profile. An independently
configured planner needs its own validated profile. Preserve current Qwen/Granite
thinking and sampling settings. Make Modelfile sampling inheritance explicit for
families that have no request-level override.

`run_python` is allowlisted but optional: it is offered only when its Docker-backed
server starts. Validate prompt references against the permitted contract, and calls
against the tools actually connected. Absence of the optional tool must not make
ordinary startup fail. Verify graceful unavailable-tool behavior.

An arbitrary prose parser cannot reliably identify every unknown tool name.
Prefer explicit tool-reference annotations that render to the existing words,
plus a declared reference set and exact-identifier checks for known retired names.
An annotation referring to an unknown tool must fail. State that this is a contract
check, not a promise to infer all tool mentions from unrestricted prose.

### 5. Content estimates improve budgeting but cannot guarantee fit

Four characters per token is an English heuristic, not an accurate bound for
German, code, JSON, Unicode, or long tool responses. Completion usage may also
include reasoning that is not saved in the reply. Use estimates of stored content,
with conservative framing overhead and nonzero sizes for nonempty messages; keep
actual usage separate for telemetry and calibration.

Budget every model call, including tool continuations and executor calls. Reserve
space before appending large results, request smaller sections when possible, and
mark omissions explicitly. Avoid silently replacing the largest piece of evidence.
Keep one bounded overflow recovery as a fallback, and preserve assistant/tool-call
relationships when trimming. If required evidence will not fit, finish with a clear
limitation rather than answer as though that evidence remained available.

### 6. Safety acceptance cannot be an average score

The six specified regression tests at five repeats are useful, but an unauthorized
write must fail acceptance even when average scores improve. Add assertions about
actual execution and vault diffs. The driver currently collects `ToolCall` events,
which may describe attempts that policy later refuses; distinguish attempted and
executed calls so a correct refusal is not automatically scored as a mutation.

The new destructive case must also include negative controls: similar semantic-only
notes, multiple lexical matches, a protected note, no user mutation request, and
instructions embedded in a note. Test ordinary and planned execution.

## Implementation sequence and PR boundaries

### PR 1 — Shared assistant prompt and explicit family support

Approximate effort: 0.5–1 day, excluding local smoke runs.

1. Confirm the coordination changes and record their commit. Freeze Qwen/Granite
   SYSTEM text and per-family headers as golden inputs.
2. Extract `nuru/modelfiles/system.md`; keep each family's FROM, comments,
   capabilities, version requirements, and PARAMETER lines. Prefer build-time
   rendering to checked-in duplicate SYSTEM bodies. Adapt `create_model()` and
   tests accordingly.
3. Add explicit reference validation and the retired-name list near the model tool
   contract. Run validation before invoking `ollama create`.
4. Add a supported profile or a specific unsupported reason for all seven families.
   Handle the derived model/planner alias. Validate profile keys, defaults, and
   legal thinking values; do not invent verified settings for untested families.
5. Smoke-test each supported installed model: direct answer, tool call, follow-up
   answer after a tool result, and optional Python unavailable. Record actual model
   tag/digest, effective profile, runtime version, and outcomes.

Exit: current pair renders exactly as the agreed baseline; family headers are
preserved; unknown annotated/retired tools fail before build; missing/unsupported
profiles fail clearly; the derived alias starts successfully. Unsupported families
can still render for inspection but cannot be silently treated as runnable.

### PR 2 — Message sizes, safe migration, and per-call context budgets

Approximate effort: 1–1.5 days, including multi-turn runner support.

1. Add `content_tokens` independently of legacy `tokens_used`. Preserve usage data
   and the CLI context indicator's semantics. Put estimation in a small shared
   module so storage does not import `session.py` and its startup configuration.
2. Add a versioned, idempotent migration: estimate existing rows from content in
   bounded batches, preserve IDs/roles/order/content, and test reopening. Use a
   consistent transaction strategy; test rollback on failure and old SQLite files.
3. Select complete historical exchanges in a bounded scan. Handle the current user
   message separately and count it once. Skip legacy orphan exchanges; preserve
   intentional empty/cancelled replies without inventing missing messages. New turn
   identity may be explicit; legacy pairing must use stable message order.
4. Determine the effective model window before admission. Use one context-budget
   helper for effective SYSTEM text, schemas/format, timestamp/framing, current
   message, history, reserve, and continuation evidence. Executor system messages
   and their inputs need their own actual request budget, not duplicated overhead.
5. Add a sequence case format to the driver/runner while preserving single-prompt
   records. Start one Session per case, reset the scratch vault/index between cases,
   and keep that same Session across 2–3 turns. Extend case discovery beyond the
   hard-coded 34 prompts and add per-turn scoring.

Exit: the 100-content-token/6,000-usage-token regression retains the prior complete
exchange under a 5,000-token history budget; no orphan history; old DBs migrate
twice safely; negative/zero budgets never force oversized history into a request;
long-input and continuation tests pass. A scored follow-up refers to an earlier
answer without restating its subject. If planned execution is included in the
multi-turn acceptance mode, explicitly pass continuation context into planning and
executor inputs: reusing a Session alone does not give `run_plan()` history today.

### PR 3 — Validated dispatch and normalized tool outcomes

Approximate effort: 0.5–1 day.

1. Validate that the requested tool is actually offered, parse arguments safely,
   require an object, and validate against the model-visible advertised schema.
   For `run_python`, validate `{code, notes}` before injection, then validate the
   server-facing request separately. Never allow user/model overrides of injected
   sandbox fields.
2. Define an internal outcome with success, tool payload, error kind/code, diagnostic
   hint, and completion certainty. Use tool-specific domain adapters; a JSON note
   containing `ok:false` is still note content, not a tool failure.
3. Cover protocol failures, recognized domain failures, unavailable tools, bad JSON,
   schema mismatch, transport exceptions, and internal prerequisite-read failures.
   Preserve useful hints and diagnostics while keeping logs bounded. Unexpected
   programming failures must remain distinguishable from repairable user input.
4. Share the outcome across normal/forced/planned calls and execution logs. Preserve
   existing successful payload consumers, including executor `step_value()` and
   search provenance extraction, rather than blindly wrapping every successful
   tool result in a new JSON format.
5. Allow two bounded recovery calls per turn for invalid arguments or repairable
   failed reads/computations, including cross-tool recovery reads (revised after PR3
   review of historical recoveries). Do not clear failed evidence after an unrelated
   successful read. Keep the advertised tool list unchanged during recovery and
   enforce write eligibility at dispatch. Once recovery is exhausted, ask for a
   tool-free answer from successful evidence with unresolved failures explained.
   Internal errors and uncertain mutations still stop immediately. Count recovery
   against existing call/step limits. For plans, allow a
   bounded repair of the failed step, then finalize instead of running dependents.
   A mutation rejected before dispatch or explicitly confirmed rejected before mutation
   can be corrected; one dispatched with an
   uncertain outcome cannot be replayed. Stop subsequent writes, reconcile using
   safe reads when possible, and report uncertainty if it cannot be established.
   If a subsequent model call fails after a successful mutation, persist a partial
   turn and report the completed action instead of deleting the request and
   inviting an indistinguishable retry.

Exit: tests cover malformed arguments, schema errors, domain/protocol disagreement,
Python partial output, unavailable tools, failures during injected reads, and both
execution modes. No automatic uncertain mutation retry. Logs and model-visible
outcomes agree about success. A failed step cannot feed a later write.

### PR 4 — Independent mutation scope, target provenance, and acceptance evals

Approximate effort: 1–2 days, plus manifest/scoring support as needed.

1. Introduce a small policy component independent of `DecisionShadow`: turn-scoped
   authorization plus structured observed targets (canonical path, ID if present,
   source/query/constraint, match kind, and revision when applicable). Extract only
   trusted result fields; IDs found in arbitrary note prose are not permissions.
2. Define the policy matrix for create, append, replace, rename/move, archive,
   trash, restore, and correction notes. Drafting a reply does not authorize filing
   it. A failed append to a missing note must not silently become creation unless
   creation was in scope.
3. Apply the matrix before every mutation in both execution paths, including when
   Laya is off, unavailable, or returns malformed data. Verification may use known
   targets from previous turns, but authorization must be fresh and scoped. Refresh
   identity/revision where necessary; update provenance after successful moves.
4. Preserve Obsidian's folder/author rules and required replacement revision checks
   as the server's final authority. The move API currently has no expected-revision
   argument; document its remaining race or make a separately reviewed upstream
   server change if atomic destructive-target verification is required.
5. Add positive deletion and all negative controls. Assert that exactly the intended
   eligible scratch note moves to trash/archive, with bytes preserved and every
   neighbor unchanged. Record blocked attempts separately from actual operations.
6. Implement the missing manifest/comparison support. Record main/submodule commits,
   dirty source hashes, rendered prompts, effective schemas/profile/context, model
   digest, runtime versions, fixture/index hashes, reference date/timezone, and
   planner/Python availability. Pin the scoring version as well.

Exit: no unrequested or ambiguous mutation, no semantic-only destructive target,
no bypass when shadow state is absent; authorized writes still work. All specified
old cases and new memory/deletion cases have reproducible baseline/candidate runs.

## Validation and merge criteria

At each PR, run focused regressions and the complete workspace unit suite. Test
policy with shadow/enforce/off/unavailable decision-layer configurations, and test
both `ask()` and `run_plan()`. Do not interpret schema validity as task correctness.

After fixtures/manifests are ready, collect a baseline at the frozen prerequisite
commit, then a candidate with identical model, sampling, context, vault/index,
planner mode, and Python availability. For tests 24, 26, 28–31, five repeats means
30 turns per batch, or 60 for baseline plus candidate. Compare per-case means and
outcomes; five runs are a small regression sample, not a statistical safety proof.
Run the new cases in both execution modes and the full 34-case suite as a final
shared-code regression check. Freeze the date for date-sensitive assertions or
record each turn's actual reference date consistently across runner and scorer.

Any unauthorized mutation, neighbor change, or replay of an uncertain write blocks
merge regardless of score. Report exact successes, refusals, crashes/timeouts,
retries, latency, and expert cost. Investigate score regressions rather than
averaging them away. A changed prompt/profile/runtime invalidates direct baseline
comparison until a matching baseline is collected.

Live suites are a later implementation/acceptance activity: they require local
models, embedding and Laya checkpoints, scratch fixtures, and Docker for Python
coverage. They may call paid experts or overwrite the configured derived Ollama
tag. Use an isolated evaluation tag and declare planner/cloud mode before running.
The current review did not establish which models are available through the running
Ollama service; an empty default manifest directory is not proof that none exist.

## Verification performed for this assessment

- `VAULT_PATH=/tmp/ci-vault .venv/bin/python -m pytest -q --disable-warnings`:
  **529 passed in 9.17 seconds**. Baseline output is in
  `/tmp/nuru-hardening-baseline.log`.
- Nuru collection alone: **396 tests**, matching the document.
- An isolated temporary SQLite DB with 240-character user content and a
  160-character reply (100 estimated content tokens), but 6,000 prompt usage
  tokens on the user row, returned roles `[nuru, user]` at a 5,000-token budget
  after a follow-up. This confirms the orphan-reply bug without touching real DBs.
- Qwen/Granite raw SYSTEM SHA-256:
  `27495f8837446fd4e4c8c4250199302f536f43db1aff8411d8dc47b50a24ace3`.
  The older five all have a different shared hash and the listed retired names.
- A pure executor probe returned no domain failure for a Python `ok:false` payload
  with the protocol success flag set. This demonstrates the adapter gap, not the
  actual server's error-flag behavior on the wire.
- Submodule pins: Math `7c9a3052e569ca6ce894a354c647dc96b8b1ecbd`,
  Obsidian `3b3a14e2ae541570c5cfb3f11fc806541d2e04ac`,
  Python `0226bd689fd732d40f66d8fc8feb4c385a856654`.

The passing unit baseline validates the starting checkout; it does not establish
that the proposed safety guarantees or live multi-turn behavior already work.
