# PR1: shared assistant prompt for Qwen3.5 and Granite4.2

Branch: `review/harness-hardening`, rebased onto `plan-executor` at `80171de`.

Qwen3.5 and Granite4.2 previously stored separate copies of the assistant prompt. Both now build from `modelfiles/system.md`, while retaining their family-specific headers, capabilities and parameters. Their complete rendered Modelfiles match the rebased baseline byte for byte. This preserves the current drafting and create-versus-append rules and avoids changing the eval baseline during extraction.

The renderer validates explicit `{{tool:name}}` references against the model tool contract and rejects known retired identifiers before invoking Ollama. Every checked-in assistant family now has an explicit support status. Missing profiles fail instead of falling back to an unverified thinking value. The derived model alias inherits its base profile; an independent planner uses its own supported profile and sampling options. Eval manifests record the effective rendered Modelfile hash as well as the template hash.

## Scope and concerns

- Supported families are `qwen3.5` and `granite4.2`. The other five assistant families are explicitly unsupported pending prompt migration and thinking validation. This narrows the document's all-seven-family migration to the requested Qwen/Granite focus.
- Tool references must use the annotation syntax in the shared source. Arbitrary unannotated unknown names in prose are not inferred; known retired names are rejected even without annotations. Tests check that the current prompt's tool references are annotated.
- `run_python` remains permitted in the prompt and is offered only if its optional server starts. Building a model does not require that server.
- Planner prompts remain separate because they have a different role. Independent planner context sizing remains existing behavior and is outside this prompt extraction.
- Family templates now require the Nuru renderer; passing them directly to `ollama create` would retain the SYSTEM placeholder.
- These smoke checks exercise live Ollama with a synthetic calculator result. They do not establish full MCP, vault, multi-turn or decision-suite quality. The document's context-budget and tool-enforcement work remains for subsequent PRs.

## Validation

Root test suite: **594 passed in 7.24 seconds**, with `VAULT_PATH=/tmp/ci-vault`. The rebased baseline was 533 passing tests. `git diff --check` passed.

Golden tests assert the full rendered baseline SHA-256 values with the original `FROM` placeholder:

| Family | SHA-256 |
| --- | --- |
| qwen3.5 | `f2879549341c47deceb485be5bfece136532633b55251c3533e989efed0945b1` |
| granite4.2 | `bd8c3e4c4ad8fd52118d4d9724cbdf385ba2e6256927afa7cc1405019553d30a` |

Live smoke results on Ollama **0.35.1**:

| Base model | Thinking | Direct answer | Tool call | Answer after result | Seconds |
| --- | --- | --- | --- | --- | --- |
| qwen3.5:0.8b | off | pass | pass | pass | 7.27 |
| qwen3.5:2b | off | pass | pass | pass | 15.26 |
| qwen3.5:4b | off | pass | pass | pass | 31.13 |
| qwen3.5:9b | off | pass | pass | pass | 56.92 |
| granite4.2:3b | low | pass | pass | pass | 20.08 |
| granite4.2:8b | low | pass | pass | pass | 45.05 |

Each model was built under a unique temporary tag, then unloaded and its temporary tag deleted. No base tag or user `nuru` tag was changed. Smokes used context size 16,384, output cap 2,048 and each family's profile sampling. Qwen request options were temperature 0.7, top_p 0.8, top_k 20, min_p 0 and presence_penalty 0; Granite retained its family Modelfile sampling.

No environment variables were set for the smokes. The inherited model-store setting was `OLLAMA_MODELS=/Volumes/Ra/Models`; `.env.nuru` was absent. The smoke script connects to `http://localhost:11434` and selects models via CLI arguments.

Reproduce from the repository root with the six base tags installed:

```sh
VAULT_PATH=/tmp/ci-vault uv run pytest
uv run python nuru/evals/model_prompt_smoke.py --models qwen3.5:0.8b qwen3.5:2b qwen3.5:4b qwen3.5:9b granite4.2:3b granite4.2:8b --out /tmp/nuru-pr1-smoke.jsonl
```

The smoke JSONL records base model digests, rendered prompt hashes, sampling and per-check outcomes. Results from this run are in `/tmp/nuru-pr1-smoke.jsonl`; test output is in `/tmp/nuru-pr1-tests.log`.
