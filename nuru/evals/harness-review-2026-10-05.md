# Nuru harness and non-planner Modelfile review — 2026-10-05

Nuru has a useful foundation for a local note assistant, especially for focused lookups, explicit appends and simple arithmetic. Its present weaknesses are primarily task routing, evidence selection and completion checks. A working calculator cannot repair omitted table rows, and a correct search result does not tell the model whether an older note has been superseded. Improving these boundaries should precede another model bakeoff or more prompt rules.

This is a source review of checkout `92f4394`, Obsidian submodule `3b3a14e`, and math submodule `cce2dba`, informed by the recorded evaluations. Planner Modelfiles are excluded. The ordinary loop's existing expert fallback is considered because it affects normal requests. No live model benchmark, cloud call or user-vault mutation was performed. The current selected configuration is Granite 4.2 3B with a 16,384-token context cap. Historical scores below are not measurements of this checkout.

## What the evidence supports

The [34-test comparison](qwen-vs-granite-check-2026-10-04.md) records Qwen3.5 4B at 132.7/170, 72/102 passes, median 39 seconds and p90 82 seconds; Granite at 126.0/170, 69/102 passes, median 42 seconds and p90 123 seconds. These are roughly 71% and 68% passes under a permissive 4/5 threshold, not dependable completion rates. Three repeats per prompt are enough to expose failure patterns, not establish a universal winner. Different thinking and sampling settings mean this compares configured systems, not model weights alone.

The [calculator checks](calculate-tool-check-2026-10-02.md) found every executed calculator result correct, but only two of six vault prompts used it. The [statistics checks](statistics-tool-check-2026-10-02.md) similarly found correct tool results while the model omitted data, sorted incorrectly, selected the wrong formula, or substituted a copied figure for verification. These findings argue for improving extraction and orchestration before changing the arithmetic engine.

Expected effectiveness of the current ordinary loop:

| Request class | Assessment | Main constraint |
|---|---|---|
| Focused lookup, latest note, tags | Strongest fit | Unnecessary first searches and ambiguous matches can add calls |
| Daily note and explicit append | Good fit | Create-versus-append intent remains ambiguous in the combined write tool |
| Arithmetic with all inputs supplied | Good fit | Tool selection and expression construction still depend on the model |
| Recompute figures from notes | Uneven | Row extraction, units, formula choice and comparison against the original claim |
| Draft a manager update | Currently under-specified | Current prompts dropped the directives-loading rule |
| Find every open task or broken link | Poor fit for exhaustive answers | Truncated retrieval and no exposed whole-vault task/link operation |
| Cross-note contradiction or engineering audit | Unreliable | Coverage, corrections, units and reasoning interact across many calls |
| Attach notes for expert review | Unsupported through the normal model tools | `ask_an_expert` is absent from the allowlist |

## 1. Model switching is mechanically supported, but the contracts differ

[session.py](../src/core/session.py) rebuilds the selected family template at startup. [profiles.yaml](../modelfiles/profiles.yaml) translates thinking levels and supplies per-request sampling. Preserving the base model's native template is a sound choice.

However, the seven non-planner Modelfiles form two prompt groups:

| Families | Current condition |
|---|---|
| Granite 4.2, Qwen3.5 | Identical current system text, approximately 1,620 whitespace-separated words; describe ten exposed tools |
| Gemma4, Qwen3, Llama3.1, Mistral Nemo, LFM2.5-thinking | Identical older system text, approximately 1,552 words; prescribe removed tools such as `search_notes`, `browse_notes`, `append_note`, and direct expert access |

This is a concrete confounder for interchangeability. A model following the older instructions can correctly follow its prompt and still call unavailable tools. Only Granite and Qwen3.5 have explicit thinking profiles; other families default to the literal string `low`. That is not a verified compatibility contract for every family. The provider does not negotiate thinking support before sending it.

**Recommendation:** keep one shared behavioral prompt and separate family sampling/thinking configuration. Render that prompt into each Modelfile, and check tool names against the actual allowlist/schema at build time. Explicitly specify thinking behavior for every supported family, then smoke-test each with a direct answer, a tool call and a tool-result continuation. Record effective settings rather than assuming the Modelfile controls everything: `chat()` overrides `num_ctx`, and Qwen's profile overrides its sampling parameters.

Several prompt rules also need resolution:

- “First action is search” conflicts with the task-specific advice to use browse, tags or exact grep, and with the direct daily-write example. Say “retrieve relevant evidence when needed,” then give task-specific first actions.
- “Never invent a path/title” conflicts with creating a newly requested note. Distinguish observed existing targets from user-authorized new names.
- “Before writing, search; if a match exists, append” does not distinguish a new standup today from an old standup about the same topic. The historical test 31 failure is consistent with this ambiguity, though it does not prove the prompt was its only cause.
- The new prompts omit `user/directives-for-local-model.md`, while test 25 explicitly requires reading it, signing correctly and respecting confidential context. Restore a trusted, explicit drafting-context rule or load that configured directive at the harness boundary.
- Current server replacements require `read_note(include_revision=true)` followed by `expected_revision`; the prompts still describe replacement without this prerequisite. The live tool description is correct, but the duplicate instructions have drifted.
- A note passage can be quoted directly according to the search section, while the core rule requires a read. Define passage-grounded quotations separately from whole-note summaries and verification.

Do not solve these by simply adding another page of rules. Remove duplicated API documentation and retain a short mandate, permission rules, evidence rules and a few useful examples.

## 2. Fix history and context accounting

In [session.py](../src/core/session.py), `prompt_eval_count` from the first model call is stored on the user message. It counts the entire prompt, including prior history and fixed overhead. [conversation_store.py](../src/core/conversation_store.py) then sums these values as though they were individual message costs. The store itself correctly documents the distinction for its context display, but its history-selection method still sums them.

Consequences include premature forgetting and incomplete exchanges. A local reproduction using a short prior user message with `tokens_used=6000`, a short assistant reply and a new message returned only the old assistant reply and new user message under a 5,000-token budget. Newly inserted user messages have no token count, so their cost is initially treated as zero during history selection.

Separately, the input-length check allocates 80% of the window to user text without subtracting the system prompt and tool schemas. Tool results accumulate until overflow; recovery discards the largest result from the last round once. That can discard precisely the evidence needed to finish an audit.

**Recommendation:** store marginal message estimates separately from whole-request usage telemetry. Budget system prompt, schemas, current request, retained evidence and output before each model call. Keep complete exchanges. Prefer section reads and bounded evidence records to dropping an entire result after overflow. Preserve source path, revision, relevant passage and correction relationships across turns: currently the conversation DB retains user/final-answer text, not the tool evidence that supported it.

## 3. Treat retrieval coverage and note authority as explicit data

[Obsidian search](../../servers/obsidian/src/obsidian_mcp/server.py) puts exact matches first and fills remaining slots with semantic matches. That is useful, but exact ranking is title/filename word count followed by path, not authority or recency. Search rows do not expose the supersession/correction graph. Ten exact matches can consume all slots, leaving no semantic results. Meaning matches always return nearest neighbors when available, even for irrelevant queries.

For tests 2, 19, 20 and 32, Nuru must discover that another note changes how an earlier statement should be interpreted. Making it independently remember to search for corrections on every question is fragile.

**Recommendation:** return explicit `superseded_by`, `corrections`, dates, source revision and match provenance with retrieved notes. Follow declared supersession links for “current” answers, while retaining old values for comparisons. Corrections should identify the claim or target they change; a newer date alone must not override everything in an older note. Retrieve linked correction passages as part of the same bounded evidence fetch.

Offer lexical-only, semantic-only and hybrid modes where appropriate, with hybrid as the usual discovery default. Exact identifiers and destructive requests should not depend on unrelated semantic neighbors. Tune any relevance threshold on labeled queries; an arbitrary cosine cutoff is not a reliable absence test. Record index freshness, and revalidate passages against current note revisions before using them as authoritative evidence.

Coverage is a separate problem: `search` caps notes at ten; `find_in_notes` caps notes at ten and matching lines at five per note; `note_info` caps lists at 25. They provide hints, but no general pagination contract. A query about “every” note or task cannot be guaranteed complete through these responses.

Expose bounded, paginated whole-vault operations for broken links and tasks, or publish the results of background jobs through a tool. `find_broken_links()` already exists internally. Include `total`, `returned`, `truncated`, cursor and scanned scope. For task resolution across prose, return candidate follow-up evidence and uncertainty; deterministic checkbox extraction alone does not establish that a later note completed an older action.

The tool purge successfully removed redundant choices. It also removed useful operations. Optimize total work per successful request, not the number of tool names: one reliable graph scan can be cheaper and more accurate than ten model-directed reads.

## 4. Strengthen calculation inputs and completion checks

The scalar calculator and statistics tools are appropriate building blocks, but they leave the hardest boundaries to the model. `statistics` currently exposes sample SD, not population SD or regression. The supplied evals show why this matters.

Add deterministic table/CSV extraction with source rows, column names, units and row counts. Let validated operations consume those extracted datasets rather than asking the model to recopy every number into JSON. Add population SD and a small linear-fit operation if those remain supported tasks. A date-difference operation also directly supports the deadline prompts, without mental month arithmetic. These are task-driven extensions, not a request for a general code-execution tool.

For “verify/recompute,” require a computation record linking source inputs, operation, result and the note's stated value. The answer must distinguish “the note says” from “recomputed.” Checking the returned count only helps if it is compared with an independently extracted source count; a model that drops a row and believes there were five can approve its own mistake.

Schema validity is necessary but insufficient. The historical forced-format eval produced valid JSON with mostly wrong arguments. Use constrained formats for fields that can be independently checked, not as evidence that the content is correct.

## 5. Repair recovery before increasing budgets

The ordinary loop enforces a ten-tool-round cap. [tier_policy.py](../src/core/tier_policy.py) permits one expert attempt at the cap. The loop then keeps its existing `tool_rounds` counter. If the model uses the advice for another tool round, the next iteration is still capped and stops because the expert was already attempted. A local policy check reproduced `expert` followed by `stop`. The fallback is therefore not a general multi-step recovery mechanism.

Reserve a final no-tools answer call that states completed and unresolved work. If an expert recovery is used, give it a small explicit recovery budget under a hard overall time/cost limit. Count both rounds and individual tool calls: one round can contain several calls. Do not solve this by removing caps.

Tool failures also need normalization. `_call_tool` returns success according to MCP `is_error`, while math can return a successful MCP response carrying domain-level `ok:false`. The normal loop discards `_call_tool`'s success flag. Exceptions are logged and re-raised; malformed JSON arguments are parsed without local recovery. Normalize protocol and domain errors into one bounded retry path, preserving the tool's diagnostic hint. Validate arguments before execution. Never automatically retry a mutation with uncertain completion; reconcile its outcome or use idempotency keys.

The current write checks validate folders and certain IDs, but do not establish that a path came from an exact match or that a mutation was requested. The “never delete a semantic-only match” rule is still primarily a prompt instruction. Track structured observed targets and their provenance; enforce destructive-target and write-intent rules independently of shadow classification. The server's folder protections, author protections and revision checks are valuable and should remain the final authority.

## 6. Separate expert routing from expert reasoning

The current normal allowlist contains ten tools and excludes `ask_an_expert`. With effort assessment off, the ordinary expert fallback is reached through the step cap. Its brief includes titles, IDs and tool statuses, deliberately excluding note content. It can advise on workflow but cannot perform the requested line-by-line engineering review from that brief.

Consequently test 33 cannot satisfy its intended expert-attachment requirement through normal authorized tool execution. Decide whether content review by an expert remains in Nuru's mandate. If it does, provide an explicit harness-controlled route with selected evidence, a bounded call budget and clear outcomes. If it does not, mark that capability unsupported and version the evaluation accordingly.

The October 4 outage is historical, not a verified present outage. The current `core.expert` path still reports only the API exception class. Preserve a sanitized API message/status/request ID for diagnosis. An HTTP 400 alone does not establish whether the cause was account state, model selection or request parameters.

## 7. Reduce avoidable latency and measure its source

Highest-value candidates, to measure independently:

1. **Fewer model decisions:** task-specific first tools, correction-aware retrieval, batch section reads and reliable aggregate operations. Allow parallel independent reads/calculations; serialize writes and dependent work.
2. **Shadow classification off the critical path:** `DecisionShadow._ask()` synchronously calls MCP, and the MCP wrapper waits on futures. Shadow decisions do not change the action, but can still delay it. Replay captured states offline or enqueue sampled shadow work. Keep deterministic write checks independent and synchronous.
3. **Cache lifecycle metadata:** each model round calls `load_model()`, including health/load checks and model-info retrieval. Cache immutable info per model digest and recheck after actual load errors. Startup currently calls `create_model()` before `ensure_server_running()`; ensure the server first. Skip unchanged builds using a rendered-template hash.
4. **Measure prefill and decoding separately:** the provider retains token counts but drops Ollama load/prompt/decode durations. Log them alongside MCP, classifier, embedding and retry times; report time to first useful output and time to a correct completed result.
5. **Tune context and sampling after correctness fixes:** compare 8k/16k/32k only on tasks that fit, and a small per-family temperature sweep. Preserve enough context for audits. Treat Qwen thinking-off as a historical baseline, not a universal model-family conclusion. Background embedding and classifier residency can compete for memory; measure contention before changing placement or schedules.

## 8. Make the evaluation distinguish success from plausible wording

Keep the [model bakeoff](model_bakeoff/bakeoff.py), but label it as a closed-context comprehension test. It supplies 13 notes and asks 27 JSON questions without tools or the Session loop. It overrides temperature/thinking, and its recovery scenario still names `search_notes`. Its score cannot predict end-to-end retrieval, mutation or recovery accuracy.

The [suite scorer](decision_layer/score_runs.py) is useful but sometimes rewards strings rather than conclusions. Test 32 accepts `periodic.attach|13.6` for “periodic attach meets it,” even if the answer says it fails. Test 34 does not require `calculate`; test 8 does not require a statistics computation. A tool appearing in the event log also need not mean successful execution: events are yielded before refusal/execution. Test 26 uses an absent target, so it does not test safe deletion of a real matching note among distractors.

Retain fast deterministic checks, then add:

- Numerical comparisons against independently calculated expectations, including required computation provenance for verification requests.
- Explicit positive conclusions and contradiction checks, with human adjudication for nuanced audits.
- Mandatory failure gates for unauthorized mutations, confidential leaks and fabricated facts; never average those away through unrelated points.
- Execution-result assertions and scratch-vault diffs, not tool-name presence alone.
- Multi-turn tasks, long notes, more than ten relevant matches, outdated indexes, conflicting corrections, revision conflicts, malformed calls, timeouts and unavailable experts.
- Held-out paraphrases and different note names, numbers and layouts. Separate development fixtures from acceptance fixtures.
- A run manifest: main/submodule commits, model digest and quantization, rendered prompt/schema hashes, effective profile/context, runtime version, vault/index snapshot, reference date and warm/cold state.

## Suggested order of work

**First:** unify prompts/profiles; restore drafting directives; document supported expert behavior; fix history accounting and capped-turn finalization; normalize errors. These remove known confounders and concrete control-flow defects.

**Next:** correction-aware evidence, safe create/append intent, paginated aggregate retrieval and validated numeric extraction. Target the recurring failures in tests 2, 6, 8, 9, 17, 19–22, 25, 31 and 32.

**Then:** compare shadow replay, batch reads, context sizes and sampling using identical manifests and held-out prompts. Start with the failing subset plus representative successful lookups/writes, then rerun all 34 prompts and the new multi-turn suite. Report exact success, critical failures, p50/p90 latency, model calls, retries and cloud cost. Speed improvements count only if correctness and mutation constraints hold.

Validation for this review: 102 existing tests passed across session, conversation store, model profiles, tier policy and MCP tools. Two isolated local checks demonstrated history selection and cap behavior. No production code or configuration changed; this document is the only added artifact. Recommendations about speed gains and future model quality remain hypotheses until measured.
