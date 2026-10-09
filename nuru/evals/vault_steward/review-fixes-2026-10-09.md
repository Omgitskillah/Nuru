# PR11 / Obsidian PR3 review fixes — 2026-10-09

PR11 was rebased onto main after PR10 merged (main 8c47624). Both sets of appended
Session tests were preserved. This round targets macOS; Windows support is deferred
at the user's request.

Nuru no longer forces whole-vault hashing or reads maintenance_status on each
indexing poll. Unused paused/status/last_success fields were removed. Optional link
or stewardship configuration errors print a warning and disable that worker,
allowing chat startup. Root pytest discovery now matches CI's five test roots.
Interactive SQLite connections retain 30-second waiting; background indexing uses
one second, so shutdown/retry stays bounded.

The backend uses mtime/size/ctime signatures plus a required persisted rolling hash
slice of 100 notes. Unchanged notes are not updated; changed signatures are batched.
Traversal prunes directories once. Invalid UTF-8 uses replacement text and raw-byte
revision hashes. Busy/unreadable notes stay pending while later notes progress.
Canonical database/state paths accept macOS aliases without accepting symlinked
SQLite files or sidecars. Digest checks remain per batch: checking only once could
mix identities if a tag changed midway through a pass.

## Evidence

- Backend/vendor suite: 667 passed, one skipped. Final integrated suite: 1,507
  passed, one skipped in 14.60 seconds.
- [No-change overhead](review-fixes-results-2026-10-09.json): five warm-cache passes
  over 2,000 disposable ~4.2 KB notes, 33.67–35.46 ms, 100 reads/pass, two metadata
  DML statements/pass, zero note updates and zero embeddings. These are not a paired
  comparison with the earlier reviewer timings.
- Compressed M4 evidence replay: all 100 candidate contracts and outcomes identical;
  source bytes unchanged, zero inference. CLI and Nuru CLI each grade 16/16; MCP tool
  registration/call succeeds. Outputs were saved under /tmp during verification.
- Both lockfiles pass offline checks. No live models, paid APIs or personal vaults
  were used in this review-fix round. Prior live contention results remain a failed
  historical gate; no new claim of passing that gate is made.

## Archived evidence

Eleven large result files retain their exact original bytes in deterministic gzip
archives (2,246,572 bytes to 320,669 bytes). The [manifest](artifact-compression-manifest.json)
records original SHA-256 hashes, verified by [the overhead evaluator](review_index_eval.py).
Small summaries remain readable; complete failed observations remain in the archives.
Writers accept .json.gz, and the replay reader handles compressed input. Duplicate PR
body documents were replaced with links to the actual PRs. Generated artifacts are
folded in GitHub diffs.

The two Nomic artifacts are retained deliberately: nomic-validation records the
pre-readiness-fix source; readiness-nomic records the post-fix source. Their source
hashes and raw runs distinguish them. Neither is a new run of the current review
fixes.

Merge order remains Obsidian PR3, then repin Nuru to the actual merged backend
commit and rerun integration checks before merging PR11. While PR3 is open, Nuru
pins backend review-fix commit 8a72029; the final main-commit repin is pending.
