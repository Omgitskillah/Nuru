# Ollama context checkpoints: investigation (2026-10-07)

Scope: source inspection of Ollama **v0.35.1**, its bundled llama.cpp **b11232**, newer release **v0.40.0 / b11351**, upstream discussions, and existing local server logs. No inference requests, settings changes, or benchmarks were made. Predictions below need the proposed isolated tests.

## Finding

**Test `num_batch: 256` first.** The observed 1,024-token fallback is tied to batch size, rather than a separate fixed 1,024-token constant. There are inherited llama.cpp environment variables for checkpoint count and minimum spacing, but reducing minimum spacing does **not** create checkpoints at that interval. No public Ollama setting was found that pins a checkpoint exactly at the system-and-tools boundary.

At the measured 227 tokens/s, 256 tokens would take about 1.13 seconds rather than 4.50 seconds, **if throughput stays the same**. Batch-size changes can alter throughput; this is an estimate, not a result. Reaching roughly 36 tokens requires a reusable checkpoint at the actual common prefix, not merely more checkpoints.

## 1. Available settings

| Setting | Interface | Meaning |
|---|---|---|
| `num_batch` | `/api/chat` or `/api/generate`: `options.num_batch`; Modelfile: `PARAMETER num_batch 256` | Ollama passes this as both llama.cpp `-b` and `-ub`. Changes prompt batching and the earlier near-end checkpoint. |
| `LLAMA_ARG_CTX_CHECKPOINTS` | Environment of the **Ollama server process**, inherited by the runner | Maximum checkpoints per slot. Default 32; llama.cpp flags `--ctx-checkpoints` / `--swa-checkpoints`. |
| `LLAMA_ARG_CHECKPOINT_MIN_SPACING_NT` | Same server environment | Minimum spacing used for selected checkpoint boundaries and eviction. Default 8192; `0` removes this minimum. Flag `--checkpoint-min-step`. |
| `num_keep` | API options / Modelfile | Preserves initial tokens during context overflow. Does not pin recurrent state at that token position. |

No corresponding `OLLAMA_*` checkpoint variable, public checkpoint API option, or checkpoint Modelfile parameter was found. The `LLAMA_ARG_*` path is supported by upstream argument parsing and Ollama's inherited environment, rather than an Ollama-specific documented contract. Restart/reload the runner after setting it and verify the startup log. A terminal environment alone will not affect an already-running macOS application/server.

The upstream [server option documentation](https://github.com/ggml-org/llama.cpp/blob/b11232/tools/server/README.md) lists both checkpoint variables. Ollama's [environment inheritance](https://github.com/ollama/ollama/blob/v0.35.1/llm/llama_server.go#L445), [batch arguments](https://github.com/ollama/ollama/blob/v0.35.1/llm/llama_server.go#L584), and [API option definitions and Modelfile parameter conversion](https://github.com/ollama/ollama/blob/v0.35.1/api/types.go) establish the paths above. `num_batch` is source-supported; it is not listed in the short public Modelfile parameter table.

At the observed 50.251 MiB per checkpoint, 32 checkpoints represent about **1.57 GiB** of checkpoint state per slot if all are populated. Increasing count can retain useful older states but does not change where new states are created.

## 2. Why the offset is 1,024

In [b11232's checkpoint placement code](https://github.com/ggml-org/llama.cpp/blob/b11232/tools/server/server-context.cpp#L3530), the two tail targets are `min(n_batch, 4 + n_ubatch)` and `min(n_batch, 4)`. Ollama sets both batch sizes to `num_batch`, so the usual targets are **end minus num_batch** and **end minus 4**.

The actual Qwen runner used by task 17791 started at 17:21:43 on October 6 with `-c 16384 -b 1024 -ub 1024`. The local log's other 512-token loads are not evidence that this runner used 512. Consequently:

- `6154 - 1024 = 5130`: checkpoint restored by task 17791.
- `6154 - 4 = 6150`: too late for its common prefix of 6118.
- `num_batch: 256` should move the earlier target to 5898 for that same prompt, provided batching, cache state, and checkpoint retention otherwise remain comparable.

Minimum spacing does not override these targets. Ordinary mid-prompt checkpoints are skipped unless a recognized user-message boundary or near-end condition permits one. Nuru's logged model uses Ollama's `qwen3.5` renderer/parser with `--no-jinja`; the completion request does not forward `message_delimiters`. Thus upstream user-boundary checkpoint logic is not an automatic solution for this path. Exposing those boundaries would require an Ollama change or using a different rendering/backend path.

## 3. Exact placement and cache APIs

Ollama already sends `cache_prompt: true` internally; adding it to public `options` is not a new cache control. `num_keep` controls context shifting, not checkpoint placement.

Standalone llama.cpp has [slot save/restore endpoints](https://github.com/ggml-org/llama.cpp/blob/b11232/tools/server/README.md#post-slotsid_slotactionsave-save-the-prompt-cache-of-the-specified-slot-to-a-file) and prompt-cache controls. These are not public Ollama endpoints and do not by themselves request an arbitrary internal checkpoint position. Using Ollama's private runner endpoint would couple Nuru to an unstable implementation interface.

There is also an [open upstream slot-restore/cache-lookup issue #28276](https://github.com/ggml-org/llama.cpp/issues/28276), so disk save/restore should not be assumed to improve hybrid prefix reuse. It is separate from the measured tail-placement problem.

## 4. Warm-up feasibility

An empty `/api/generate` prompt [returns after loading](https://github.com/ollama/ollama/blob/v0.35.1/server/routes.go#L516); an empty chat message list similarly loads without prefilling. Neither warms the system/tool prefix.

Standalone llama.cpp documents `n_predict: 0` as prompt evaluation without generation. However, Ollama's rendered completion request declares [`n_predict` with `omitempty`](https://github.com/ollama/ollama/blob/v0.35.1/llm/llama_server.go#L1405). Setting `options.num_predict: 0` leaves zero out of the forwarded request, so this is **not a dependable prompt-only request for Nuru's current path**. Ollama's native-chat path includes zero explicitly, but that is a different path and has not been tested here.

Candidate: a **nonempty, one-token warm-up** using `num_predict: 1`, thinking off, the same model, context size, batch size, tools, and stable system text. Discard its generated output.

The critical condition is token-prefix identity. A system-only chat request may add an assistant header before its end; the real request adds a user header instead. Its end-minus-four checkpoint could therefore still lie beyond the common prefix. Compare the rendered tokens/LCP and restored checkpoint; do not assume system-only means the correct checkpoint. A raw request containing the exact stable rendered prefix may avoid that header difference, but requires Nuru to reproduce the same rendering and BOS behavior.

Checkpoint state stays available while the runner/slot retains it, subject to invalidation, eviction, and unload. `keep_alive` can keep the runner resident; it does not pin a checkpoint. Warm-up overlaps startup only if loading Laya does not evict Qwen or erase the latency saving through resource contention. Include warm-up time in total startup measurements.

## 5. First call versus follow-up

The log supports a **divergent-prefix** explanation, rather than a universal per-call penalty:

| Task | Prompt tokens | Common prefix | Restored tokens | Evaluated tokens |
|---|---:|---:|---:|---:|
| 17791 | 6154 | 6118 | 5130 | 1024 |
| 17823 | 7360 | 6150 | 6150 | 1210 |
| 17858 | 8150 | 7356 | 7356 | 794 |
| 18048 | 6154 | 6150 | 6150 | 4 |

The two growing-prompt calls restore exactly at the logged common prefix; they do not incur an additional 1,024-token rewind. The later repeated short request evaluates only four tokens. Task IDs alone do not establish application session identity, so this is evidence about contiguous model requests, not a measured classification of every session.

Follow-ups can still suffer if Nuru changes earlier text, drops reasoning, rewrites tool messages, trims history, or the runner loses cache state. Upstream [issue #24890](https://github.com/ggml-org/llama.cpp/issues/24890) reports checkpoint invalidation across tool calls and a subsequent user message on an older build. Inspect `common prefix - restored tokens` to separate unnecessary rewind from genuinely new tool/result text.

## 6. Relevant versions and upstream work

- [llama.cpp PR #20288](https://github.com/ggml-org/llama.cpp/pull/20288): near-end checkpoint placement and the four-token tail. Referenced directly by the bundled placement code; already included in Ollama 0.35.1.
- [llama.cpp PR #25472](https://github.com/ggml-org/llama.cpp/pull/25472): spacing-based checkpoint eviction, related to [issue #25023](https://github.com/ggml-org/llama.cpp/issues/25023). Already represented in b11232.
- [llama.cpp PR #28302](https://github.com/ggml-org/llama.cpp/pull/28302): corrects eviction for prompts shorter than the spacing limit and replaces duplicate checkpoint positions. **Already included in b11232**, as verified in `create_checkpoint`; upgrading solely to get this fix would not change the observed result.
- [Ollama 0.33.0](https://github.com/ollama/ollama/releases/tag/v0.33.0): fixes cancelled-prefill restore points and recurrent-state coverage, and removes a changing Claude Code system-prefix countdown. These changes precede 0.35.1 and do not change the two tail offsets.
- [Ollama 0.35.1's pin](https://github.com/ollama/ollama/blob/v0.35.1/LLAMA_CPP_VERSION) is b11232. [0.40.0's pin](https://github.com/ollama/ollama/blob/v0.40.0/LLAMA_CPP_VERSION) is b11351. The latter still has the same tail-offset algorithm; no specific fix for this 1,024-token scenario was identified in that comparison.
- [Ollama 0.40.0](https://github.com/ollama/ollama/releases/tag/v0.40.0) makes MLX the default for supported architectures on Apple Silicon, including Qwen3.5. That is a relevant separate backend experiment, not evidence that the llama.cpp checkpoint issue is fixed. Record actual backend and model format; the llama.cpp settings above do not govern MLX.
- [Ollama issue #17434](https://github.com/ollama/ollama/issues/17434) includes a report of using `LLAMA_ARG_CTX_CHECKPOINTS=0` through Ollama to avoid a hybrid-model checkpoint crash. It corroborates the inherited-variable route, but disabling checkpoints is not a speed remedy for this case.

## Isolated candidates and measurements

1. **Batch size 256**, keeping everything else fixed. Verify runner arguments `-b 256 -ub 256`; record cold long-prompt throughput as well as the second session's first-call latency. A smaller batch may trade cached-call savings for slower cold prefill.
2. **Minimum spacing 256**, keeping the original batch size and count. Verify the startup line. Treat this as a retention/boundary experiment, not a predicted 256-token cadence. Do not increase count simultaneously.
3. **Exact-prefix, one-token warm-up**, keeping original settings. Check that a retained checkpoint sits at or before the real common prefix, and measure both startup cost and first-call savings.
4. **0.40.0 / MLX**, separately from llama.cpp tuning. Hold prompt, model quality/quantization, context, and scoring criteria comparable and explicitly record backend changes.

Use **different user messages after an identical stable prefix**. Repeating the complete same prompt can hit the end-minus-four checkpoint and produce a misleading four-token result, as task 18048 already demonstrates. For every call capture prompt length, LCP, restored position, evaluated tokens, duration, runner reloads, and checkpoint eviction. The useful overhead is `LCP - restored`, not the whole `prompt_eval_count`.
