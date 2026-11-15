# F40 context-memory control: implementation in progress

The user confirmed per-model context memory in 1 GB steps, opened from F18’s ring,
for 1.0.5. GB means decimal 1,000,000,000 bytes. This describes variable context
allocation, not the model weights or total process RAM.

## Implemented foundation

`core.context_memory` is model-free. Explicit supported model metadata predicts
f16 K/V bytes per token. Qwen 3.5 explicit recurrent flags override the full-attention
interval. Unknown architectures, sliding/shared-KV and prediction-layer layouts,
missing head dimensions, and unknown/unsupported runner cache precision refuse.
Two isolated actual resident-size samples (at most 16k, at least 4k apart) must
agree with the geometry within 5%; the tolerance is provisional pending calibration
and review. Identity includes digest, server version and cache type; geometry changes
also invalidate the conversion. Actual adapters must verify loaded windows, unchanged
placement and parallelism; requested sizes alone are not measurements.

## Still to implement and verify

- Trustworthy actual-runner precision/parallelism discovery. Never assume Nuru’s
  environment describes an independently started Ollama server.
- Safe calibration adapter under the canonical shared-compute protocol. Preserve the
  user’s resident model; no live inference has been run for this work yet.
- Minimum window measured from shipped system/tools plus bounded read/reply headroom.
- Atomic per-model persistence outside the install, revision conflicts, strict inputs,
  calibration identity validation and invalidation.
- Server-side exclusive reload reservation refusing turns, unwinding workers, title
  jobs and queues; do not hold service locks across blocking network I/O. Preserve
  the previous setting on failures and report uncertain runner state honestly.
- Explicit NURU_MAX_NUM_CTX override wins; the unset 32k default must not accidentally
  cap the user’s saved choice. Next turns/titles/model loads must use the same window.
- Ring panel, accessible buttons, per-model bounds, disabled Claude explanation,
  reload status clearing stale readings, honest partial-GPU/unknown residency notice.
- Useful integration/security/failure tests, local suites and coverage gate; only
  Windows-specific CI if needed. Claude’s thorough review and manual UI checklist.

## Primary references

- [Ollama running models API](https://docs.ollama.com/api/ps): resident size,
  GPU size, digest and actual context length. This API does not establish cache precision.
- [Qwen 3.5 loader](https://github.com/ggml-org/llama.cpp/blob/master/src/models/qwen35.cpp):
  explicit recurrent flags take precedence; otherwise full attention at interval boundaries.
- [Ollama FAQ](https://github.com/ollama/ollama/blob/main/docs/faq.mdx): cache precision is a
  server configuration. The initial implementation refuses non-f16 rather than guessing.
