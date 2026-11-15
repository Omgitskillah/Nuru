# F18 context status — implementation plan

Brief: https://trello.com/c/Tvyw5PGF. Branch: codex/f18-context-meter, from main after R14 #92. The Session callback, persisted status event and panel display are implemented on this branch; validation and Claude review remain required. See f18-check-by-hand.md for pending manual checks.

## Data and meaning

Show the last completed main-model call's context usage against its configured window. For Ollama, use the estimate of the actual fitted request (system, tools, format and message framing included) plus reported completion usage, explicitly marked estimated. For Claude, use normalized provider usage, including cached input exactly once. Never use cumulative billing totals or log scraping. Label the presentation approximately (for example “About 11k of 16k context”). Missing counters/window are unknown, never zero. Do not infer content loss from fullness or change the context budget algorithm.

A sticky per-turn notice records actual omission of older history or tool evidence. Audit all real paths: budget-limited initial history selection, _chat's old-exchange/evidence eviction, and the bounded provider-overflow tool-result replacement. A conservative estimate alone never causes a warning. Keep the notice after the final call and replay/reload. Multiple requests may drop the same copied history/results; do not sum those repeated removals as distinct items. Prefer explicit categories/booleans unless distinct omissions can be identified reliably.

## Transport and UI

Propose a reusable, strictly validated status event carrying an optional context object. Keep the payload extensible for later WP01-ACT2 without implementing its thinking/step budgets now. Add equivalent v1/v2 schemas, Python and JavaScript Display handling, persisted final snapshots and replay-safe event application. Old saved histories without context still load. Verify older clients' unknown-event behavior before choosing an additive wire event; do not assume compatibility from the version number alone.

Session reports typed ContextStatus records through on_context, alongside its existing tool-outcome callback; reply/CLI event sequences stay unchanged. The host/protocol translate it into the status event. Do not inspect mutable logger internals or share usage between conversations. Each turn starts with unknown usage and no omission notice; model switches and pending calls must not show stale values as current. Render through text-safe UI, without replacing the composer or selection. A notice suggests starting a new conversation but never does so automatically.

## Verification

Use fake provider streams and disposable history. Small-window tests must actually omit both old history and a tool result, and leave original stored history unchanged. Cover every omission path, no-omission requests, missing/zero usage, normalized cached tokens, failed and cancelled calls, multiple model calls with last-call usage rather than accumulated totals, repeated omission without inflated counts, conversation/model switching, persisted snapshots, compacted histories, replay and old histories. Test schema limits and useful UI behavior. Run local tests/build and the combined coverage gate; hosted tests only when Windows-specific behavior is affected. Include a Check by hand checklist and request Claude's thorough review before merge.

## Provider review notes (2026-11-15)

Claude agrees with the design and requests an Ollama regression with a large sent prompt and tiny prompt_eval_count. Use the fitted-request estimate for the local meter; do not assume every Ollama version's counter has cache-exclusive semantics. Current upstream usage docs describe prompt_eval_count as full input size and expose a separate prompt_eval_cached_count (https://github.com/ollama/ollama/blob/main/docs/api/usage.mdx). Claude's cache benchmark observation may be version-specific; no local model trial is required for this UI work.

Current Nuru sends truncate:false (core/providers/ollama.py) and require_fit refuses an oversized estimated request before sending. Preserve both. An estimate exceeding a window is not proof the server omitted text: display unknown/over-budget status if needed, not an actual-omission notice or an unsupported “may have been cut” claim. Actual history/tool removals get the sticky notice. Claude normalization already includes cache-read and cache-creation input; do not add them twice.
