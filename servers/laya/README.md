# laya-mcp

Nuru's decision layer. [Laya](https://huggingface.co/convaiinnovations/laya) is a small "System One" decision model: given a **state** and some **typed questions** it returns an answer with probabilities for each question, in one forward pass, and never generates text. This server runs it in-process over stdio MCP (no network ports) and turns its probabilities into decisions using thresholds from [`config/decision_layer.yaml`](../../config/decision_layer.yaml).

The split of work: **Laya returns probabilities, the config and the code own the policy, Nuru's harness enforces it.** Laya never decides on its own, and the model never calls the decision tools.

## Setup

```bash
cp .env.example .env              # VAULT_PATH, for classify_notes (read-only)
uv sync
uv run laya-mcp-fetch             # once: downloads the pinned checkpoints (~1.5 GB)
uv run laya-mcp                   # stdio server; Nuru starts it itself
```

The server runs with the Hugging Face hub offline (`laya.offline: true`), so after `laya-mcp-fetch` it never touches the network. The checkpoints are pinned to one commit (`laya.revision`).

To try the tools by hand in the MCP Inspector, pass the vault with `-e` - the Inspector starts the server with its own environment, so a `VAULT_PATH` set in your shell never reaches it (and `classify_notes` fails with "no vault is configured"). `health` reports `"vault": true` once it has one, and `laya.state: "ready"` once the checkpoints have loaded (~10 s):

```bash
MCP_INSPECTOR_LOG_DIR=./.logs npx @modelcontextprotocol/inspector -e VAULT_PATH=/absolute/path/to/vault uv run laya-mcp
```

## Tools

Harness only - Nuru calls these and acts on the answer in code; they are never in the model's tool list:

| Tool | Returns |
|---|---|
| `tool_gate(phase="start", request, tools)` | `mode` (answer / read / write / ask_user) and `first_tool`. `answer` needs a high threshold; below it the default is `read`, with `tool_gate.default_first_read` first. |
| `tool_gate(phase="next", request, tools, steps)` | `next`: a tool name, `answer`, `ask_user`, or `defer` (Laya unsure or down: the model keeps choosing). |
| `assess_effort(request, step_type, steps, failures)` | `tier` (off / low / medium / high / expert): the predicted tier if its probability clears that tier's threshold, else `tiers.default`. The within-task rules (tiers only go up, failure signals, expert budget) are the harness's. |
| `gate_write(request, tool, arguments, known_ids)` | `verdict`: `block` (a code check failed - `reasons` says which), `proceed` (both Laya answers cleared their thresholds) or `ask`. |
| `check_claim(reply, log, known_ids)` | `passed`, `verified` and `failures`. A note ID in the reply that isn't in the log or `known_ids` fails straight away, without Laya. |
| `rerank(query, matches)` | `semantic_search` matches reordered by Laya's `relevant` probability, top `rerank.top_k` kept. |
| `health()` | Laya's load state, device, loaded checkpoints, question-set versions, decision-point modes. |

For the model: **`classify_notes(question_set, ids | folder | date_from/date_to, labels)`** returns `{count, labelled: [{id, label, p}], uncertain}` - confident labels only, plus how many weren't. It never returns note content. `id` is the note's frontmatter id, or its vault-relative path if it has none. At most 50 notes per call.

Every answer carries `probabilities` and `source` (`laya`, `code` or `fallback`), and a `decision_id` the harness uses to record what it actually did.

### Fail safe

If Laya is still loading, failed to load, raises, or an input is over its token budget, every tool still answers - conservatively - with `source: "fallback"`: `tool_gate` gives `read` / `defer`, `assess_effort` the default tier, `gate_write` `ask` (Nuru blocks in unattended runs), `check_claim` its code checks only with `verified: false`, `rerank` the search's own order. `classify_notes` reports that it isn't available.

## Question sets

One YAML file per decision point in [`src/laya_mcp/questions/`](src/laya_mcp/questions), in the `POST /v1/systemone` question shape (kept so the backend could be swapped for Jev) plus `name`, `version`, `checkpoint`, `max_state_tokens` and optionally `max_len` / `head_max_len`. All are validated at startup.

- **Options known only at request time** are named with `criteria_from`: the live tool list for `tool_gate`, the note types and the caller's labels for `classify_notes`.
- **`only_when`** asks a question only when the caller sets a flag: `hides_failure` is only asked when a write failed.
- **Bump `version`** whenever a question's wording or options change. It is logged with every decision and exported with every label, so fine-tuning data never mixes wordings.

### classify_notes: note_type and subject

- **`note_type`** options are the eleven types from `obsidian_mcp.frontmatter_template.TYPE_FOLDER` - the same list the vault's Templates README defines - read at request time, so they can't drift. The note's frontmatter is stripped before classifying (it names the type).
- **`subject`** options are **the labels the caller passes** (2-10 of them), e.g. "firmware / personal / career". Nothing is derived from the vault's folders or tags. This was a deliberate choice: the folder layout differs between vaults (dummyVault's `nuru/` folders just repeat the note types; the real vault uses its own), and the tags in use are too noisy to be subjects (`sample`, `analysis-test`, `412`). Caller-supplied labels work on any vault, and Laya's options are defined per request by design.

Results are cached by (note id or path, content hash, question set and version, options), so an unchanged note is never classified twice for the same labels.

## Token budgets

Laya cuts over-long input off **silently**. So before every call the state is counted with the answering checkpoint's own tokenizer, and the full sequence (questions + options + state) is measured the way Laya builds it; anything that wouldn't fit is rejected (`over_budget`) and the decision falls back. JSON costs far more tokens than its length suggests (measured: 2.75 characters per token, against 4.5 for prose).

| Checkpoint | Context | Used for |
|---|---|---|
| english | 512 | tool_gate, assess_effort, gate_write, rerank |
| multilingual | 1024, or up to 8192 with `max_len` | check_claim (4096), classify_notes (8192) |

English question sets still go through Laya's Router, which sends non-English text to the multilingual checkpoint: the English checkpoint collapses on other scripts. Both are preloaded and stay loaded.

## Calibration

Laya's probabilities mean nothing until calibrated on Nuru's own labelled decisions. The multilingual checkpoint ships no fitted calibration at all, and the English one ships a 0.10 temperature for choices with 11+ options, which Laya itself flags as uncalibrated - `tool_gate`'s tool choice is one of those. So:

- every threshold in the config records its calibration (date, labels, held-out labels);
- **the config refuses to load** with a decision point in `enforce` while any threshold it uses is uncalibrated;
- everything ships in `shadow`, logging what Laya would have done next to what happened.

## Decision log

SQLite at `database` in the config - `nuru/.db/decisions.sqlite3` by default, next to Nuru's conversation history - in WAL mode because the harness writes to it too. It holds state text, which can include vault content, so it is hidden, gitignored (the root `.gitignore` also names `decisions.sqlite3*` as a safety net) and never leaves this machine.

- `decisions` - every call: session/task/step, question set and version, checkpoint, state hash and text, questions, answers, status, the action the policy proposed, the action the harness took, shadow/enforce, latency, outcome, label and label source (`user` or `claude` only - never the local model).
- `tool_calls` - what the harness actually executed. Note text in `content`/`body`/`text` arguments is stored as its length and hash; IDs and paths are kept.
- `classify_cache` - see above.

## Generated files

All hidden and gitignored (the repo-wide list is in the [root README](../../README.md#generated-files)):

- `nuru/.db/decisions.sqlite3`, with SQLite's `-wal` and `-shm` beside it - the decision log above.
- `servers/laya/.logs/` - one MCP access log per client session (`LAYA_MCP_LOG_DIR`).
- Laya's checkpoints live outside the repo, in the Hugging Face cache (`~/.cache/huggingface/`), put there by `laya-mcp-fetch`.

## Privacy

Laya runs in this process on this machine. Nothing here calls a network service (the hub is offline at runtime), and no API key is read or logged.

## Dependencies

- **`laya`** (0.3.21, pinned; Apache-2.0) - the decision model runtime. Installed without extras: its `mcp`, `serve`, LangChain/LangGraph, CrewAI and LlamaIndex extras are not used. It brings, and cannot run without:
  - **`torch`** (~560 MB) - runs the model. There is no torch-free path: `laya.common` imports torch at module level, so even Laya's ONNX runtime needs it.
  - **`transformers`** (~58 MB) - the ModernBERT / mmBERT encoders and their tokenizers (with **`tokenizers`**).
  - **`safetensors`** - reads the weights without executing any code in them.
  - **`huggingface_hub`** - downloads the checkpoints (`laya-mcp-fetch` only; offline at runtime).
  - **`numpy`** - Laya's probability maths.

  Together about 750 MB installed, almost all of it torch.
- **`pydantic`** - validates the config and the question sets at startup.
- **`pyyaml`** - reads them.
- **`mcp`**, **`access-logging`**, **`python-dotenv`** - as in the other servers.
- **`obsidian-mcp`** (workspace) - only for `frontmatter_template.TYPE_FOLDER`, the note types.

## Testing

```bash
uv run pytest
```

The tests use a fake predictor, so they need neither Laya's weights nor torch to be loaded.
