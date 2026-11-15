# F40 context-memory control

The user confirmed a per-model setting, decimal 1 GB steps, staged changes, one reload
on Apply, and discard on close. The maximum is the largest window actually verified to
fit on this computer, capped by model/admin limits. The panel says which limit applies
and keeps CPU/mixed residency distinct from fully GPU-resident operation.

## Implemented

- The ring opens the approved measured/unmeasured popover. GET only reads metadata.
  Measure is explicit; minus/plus only stage values. A newly measured suggestion can
  Apply without first changing its rounded GB value. Navigation/close ignores stale
  responses. Lost reload responses are not retried; reopen to reconcile.
- Authenticated local GET/PUT `/v1/models/context`, strict schema, model binding,
  revision checks and idle-service admission. Remote listeners cannot administer models.
  Requests may not overlap active/queued/unwinding turns, titles or another reload.
- Cross-process compute lease, no automatic expiry or stale-lock deletion. A timeout
  retains ownership; failed restore is uncertain, never successful measurement. Further
  model work refuses until the uncertain reservation is reconciled. Development live
  runs must also follow the canonical shared-compute protocol and announcement.
- Real Ollama operations: bounded empty load requests, actual context/residency checks,
  observable digest/version/metadata fingerprint. Other resident models are not evicted.
  Cross-platform available-memory data comes from psutil; dedicated VRAM is not treated
  as reclaimable system RAM. Apple Silicon unified memory and CPU residency are explicit.
- Two cold slope probes (8k/16k), at most two bounded candidate loads, mandatory restore
  outside measurement progress. Missing memory data or unaffordable probes refuse.
  Candidate budget applies the physical-memory ceiling once, available-memory ceiling,
  fitted baseline and headroom. A proposal is not a verified maximum until loaded.
- Supported geometry is a plausibility gate, not a claim of KV precision/parallel slots.
  Measured effective slope includes runner-buffer growth. Runtime checks compare context
  residuals with the fitted intercept; no conversion goes beyond the verified envelope.
  Qwen 3.5 recurrent flags precede interval metadata. Plain Granite uniform heads are
  supported; unsupported sliding/shared/prediction layouts refuse.
- Bounded calibration evidence and revisioned per-model choices live outside install.
  Atomic file publication/fsync, plus POSIX directory fsync; unreadable/newer data is
  preserved. Identity, geometry and post-load checks prevent silently reusing stale data.
- Session load, next chat rounds and title window selection use the same runtime policy.
  Resident window mismatch reloads before a call. Status-only model inspection never
  reloads. Explicit NURU_MAX_NUM_CTX wins; the unset 32k default does not cap a saved choice.
- Apply verifies before saving. Save failure keeps the verified live choice with an
  explicit restart-size notice and categorized logs. Reload failure attempts rollback;
  failed rollback remains uncertain. Partial-GPU operation gets the slower-replies warning.

## Still required before readiness

- Full local suite/coverage validation on the integration commit and focused Windows
  checks for file publication, ownership and memory probing. No broad hosted Linux tests.
- Claude's thorough security/concurrency/failure review of the production adapter.
- Native bounded acceptance under a coordinated reservation, on disposable test state,
  without changing the user's live service/vault or unloading another client's model.
- Native macOS panel checks and the user's Windows checklist; no scripted response or
  mockup may be presented as end-to-end model acceptance or a real screenshot.
- Review uncertainty recovery/operator messaging and memory preflight conservatism on
  supported hosts. The 96MB/10% residual tolerance is based on bounded observed Qwen and
  Granite data, not a general hardware guarantee. Unsupported cases remain unavailable.

## References

- [Ollama running models](https://docs.ollama.com/api/ps): actual resident size, GPU size,
  digest and context; this does not expose hidden cache precision or parallelism.
- [Qwen 3.5 loader](https://github.com/ggml-org/llama.cpp/blob/master/src/models/qwen35.cpp):
  recurrent flags take precedence over interval boundaries.
- [Granite loader](https://github.com/ggml-org/llama.cpp/blob/master/src/models/granite.cpp):
  uniform standard-attention head dimensions for the supported plain architecture.
