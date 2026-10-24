# laya-mcp

Nuru's decision layer. [Laya](https://huggingface.co/convaiinnovations/laya) is a small "System One" decision model: given a **state** and some **typed questions** it returns an answer with probabilities for each question, in one forward pass, and never generates text. This server runs it in-process over stdio MCP (no network ports) and turns its probabilities into decisions using thresholds from [`config/decision_layer.yaml`](../../config/decision_layer.yaml).

The split of work: **Laya returns probabilities, the config and the code own the policy, Nuru's harness enforces it.** Laya never decides on its own, and the model never calls the decision tools.

## Setup

Laya is off on main, so a plain `uv sync` leaves its model runtime out. To run it:

```bash
uv sync --all-extras              # adds the `model` extra: laya, with torch (~720 MB)
uv run laya-mcp-fetch             # once: downloads the pinned checkpoints (~1.5 GB)
uv run laya-mcp                   # stdio server; Nuru starts it itself
```

The server runs with the Hugging Face hub offline (`laya.offline: true`), so after `laya-mcp-fetch` it never touches the network. The checkpoints are pinned to one commit (`laya.revision`).

To try the tools by hand, use the MCP Inspector. `health` reports `laya.state: "ready"` once the checkpoints have loaded (~10 s). Laya reads no notes (`classify_notes`, its one model tool, was removed on 2026-10-03), so it needs no vault:

```bash
MCP_INSPECTOR_LOG_DIR=./.logs npx @modelcontextprotocol/inspector uv run laya-mcp
```

## Tools

Harness only - Nuru calls these and acts on the answer in code; they are never in the model's tool list:

| Tool | Returns |
|---|---|
| `tool_gate(phase="start", request)` | `mode` (answer / read / write / ask_user). `answer` needs a high threshold; below it the default is `read`. `first_tool` is code's, not Laya's: for a read or a write, `tool_gate.default_first_read` - search first. |
| `tool_gate(phase="next", request, steps)` | `next`: `continue` (use another tool - which one stays the model's choice), `answer`, `ask_user`, or `defer` (Laya unsure or down: the model decides). Each step is `{tool, status, result}`, the result a one-line summary of what it returned. |
| `assess_effort(request, step_type, steps, failures)` | `tier` (off / low / high / expert - there is no medium: on Granite it was identical to high): the predicted tier if its probability clears that tier's threshold, else `tiers.default`. The within-task rules (tiers only go up, failure signals, expert budget) are the harness's. |
| `gate_write(request, tool, arguments, known_ids)` | `verdict`: `block` (a code check failed - `reasons` says which), `proceed` (both Laya answers cleared their thresholds) or `ask`. |
| `check_claim(reply, log, known_ids)` | `passed`, `verified` and `failures`. A note ID in the reply that isn't in the log or `known_ids` fails straight away, without Laya. Otherwise Laya reads the reply alone and says what it claims (`made_change` / `reports_failure` / `no_change`), and code checks that against the log: a claimed change with no successful write behind it fails. |
| `rerank(query, matches)` | `search`'s matches by meaning reordered by Laya's `relevant` probability, top `rerank.top_k` kept. |
| `note_type(title, content)` | A new note's type, judged from its title and text against `TYPE_DESCRIPTIONS`; `type` is set only when the choice clears its threshold. Nuru asks before each `write_note` that names a note and logs the model's own type as the action (`model:<type>`); enforced, it fills in the type when the model gave none (never `correction`). Shadow until calibrated: [note-type check](../../nuru/evals/note-type-check-2026-10-03.md). |
| `health()` | Laya's load state, device, loaded checkpoints, question-set versions, decision-point modes. |

Every answer carries `probabilities` and `source` (`laya`, `code` or `fallback`), and a `decision_id` the harness uses to record what it actually did.

### How Nuru uses it

Nuru starts this server with the others, asks at every decision point, and records what it actually did against each `decision_id` (`action_taken`, plus `outcome` - `empty_reply`, or `ok` / `error` for a write). A point in **shadow** changes nothing; a point the config sets to **enforce** is acted on, and one set to **off** isn't asked, as [Nuru's README](../../nuru/README.md) describes. Since 2026-09-30 every point that asks Laya is in shadow or off; the enforced ones (`step_cap`, `write_checks`, `watch_thinking`) are decided by code alone. The hooks are in [`nuru/src/core/decision_shadow.py`](../../nuru/src/core/decision_shadow.py), the enforce paths in [`enforcement.py`](../../nuru/src/core/enforcement.py).

### Fail safe

If Laya is still loading, failed to load, raises, or an input is over its token budget, every tool still answers - conservatively - with `source: "fallback"`: `tool_gate` gives `read` / `defer`, `assess_effort` the default tier, `gate_write` `ask` (Nuru blocks in unattended runs), `check_claim` its code checks only with `verified: false`, `rerank` the search's own order.

## Question sets

One YAML file per decision point in [`src/laya_mcp/questions/`](src/laya_mcp/questions), in the `POST /v1/systemone` question shape (kept so the backend could be swapped for Jev) plus `name`, `version`, `checkpoint`, `max_state_tokens` and optionally `max_len` / `head_max_len`. All are validated at startup.

- **Options known only at request time** can be named with `criteria_from` (no current question set uses it; `classify_notes` did).
- **Small option sets, narrow questions.** Version 1 asked `tool_gate` to pick among Nuru's 26 tools and `check_claim` to compare a reply with a log; zero-shot, the first matched the model 10% of the time and the second flagged 88% of honest replies (2026-09-28 evals, [results](../../nuru/evals/decision_layer/results-2026-09-28.md)). Version 2 keeps every choice to a few options and asks Laya only what it can judge from the text in front of it; code does the rest.
- **Bump `version`** whenever a question's wording or options change. It is logged with every decision and exported with every label, so fine-tuning data never mixes wordings.

## Token budgets

Laya cuts over-long input off **silently**. So before every call the state is counted with the answering checkpoint's own tokenizer, and the full sequence (questions + options + state) is measured the way Laya builds it; anything that wouldn't fit is rejected (`over_budget`) and the decision falls back. JSON costs far more tokens than its length suggests (measured: 2.75 characters per token, against 4.5 for prose).

| Checkpoint | Context | Used for |
|---|---|---|
| english | 512 | tool_gate, assess_effort, gate_write, rerank |
| multilingual | 1024, or up to 4096 with `max_len` | check_claim (4096) |

English question sets still go through Laya's Router, which sends non-English text to the multilingual checkpoint: the English checkpoint collapses on other scripts. Both are preloaded and stay loaded.

## Calibration

Laya's probabilities mean nothing until calibrated on Nuru's own labelled decisions. The multilingual checkpoint ships no fitted calibration at all, and the English one ships a 0.10 temperature for choices with 11+ options, which Laya itself flags as uncalibrated - why no question set asks for more than a handful of options. So:

- every threshold in the config records its calibration (date, labels, held-out labels);
- **the config refuses to load** with a decision point in `enforce` while any threshold it uses is uncalibrated;
- every point that asks Laya ships in `shadow` (or `off`), logging what Laya would have done next to what happened; only the points decided by code alone (`step_cap`, `write_checks`, `watch_thinking`) are enforced.

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

- **`laya`** (0.3.21, pinned; Apache-2.0), in the optional `model` extra - the decision model runtime. Installed without its own extras: its `mcp`, `serve`, LangChain/LangGraph, CrewAI and LlamaIndex extras are not used. It brings, and cannot run without:
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
