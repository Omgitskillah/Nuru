# nuru

A chat app backed by a local [Ollama](https://ollama.com) model, as a terminal CLI (`nuru-cli`) or the Obsidian plugin's service (`nuru-serve`). Type a line, hit enter, and it's sent straight to the model; the reply streams back live, prefixed with `[nuru]` in the CLI.

## Setup

Requires [Ollama](https://ollama.com) installed (`ollama` on your `PATH`) and at least one model pulled, e.g.:

```bash
ollama pull granite4.2:3b
```

Nuru's settings live in `.env.nuru` in the repo root (see [Settings](../README.md#settings)). From the root, copy the template and adjust if you want a different default model:

```bash
cp .env.nuru.example .env.nuru
```

```
NURU_BASE_MODEL=granite4.2:3b
# NURU_BASE_MODEL=qwen3.5:4b
MODEL=nuru
```

- `NURU_BASE_MODEL` - the plain Ollama model to build Nuru's custom model from (must already be pulled).
- `MODEL` - the model actually loaded and chatted with. On every startup, `Session.start()` runs `ollama create $MODEL -f modelfiles/<family>.Modelfile` from `NURU_BASE_MODEL`, so `MODEL` should be a distinct, derived tag (default `nuru`) rather than `NURU_BASE_MODEL`'s own name - setting them to the same value makes `ollama create` try to rebuild a model from itself.

Switching models is changing `NURU_BASE_MODEL`: everything else that has to change with it comes from the family's files (see [the model's settings](#the-custom-model-and-its-settings)), including how much it thinks - there is no `THINK` setting any more.

## The custom model and its settings

Qwen3.5 and Granite4.2 build from one assistant prompt, [`modelfiles/system.md`](modelfiles/system.md). Their family Modelfiles keep their own `FROM`, capabilities, comments and sampling parameters, with `SYSTEM """__SYSTEM_PROMPT__"""` filled in by [`core.modelfile`](src/core/modelfile.py) before `ollama create`. Use Nuru's build path to resolve this template; passing the family template directly to Ollama would leave the placeholder as the prompt.

In the shared source, `{{tool:search}}` renders to `search`, so explicit tool references can be checked against [`MODEL_TOOLS`](src/core/tool_contract.py) without changing the words the model sees. Unknown references and exact retired tool names fail before a model is built. `run_python` is permitted in the prompt but offered only when its optional server starts; its absence does not prevent model construction. The harness's expert tools are excluded from the model's contract.

`session._modelfile_for(base_model)` picks the file by family - `NURU_BASE_MODEL` with any registry namespace and `:tag` stripped, so `qwen3.5:9b` builds from [`modelfiles/qwen3.5.Modelfile`](modelfiles/qwen3.5.Modelfile). A base model with no file of its own raises `FileNotFoundError` listing the families that do have one, rather than falling back: the `PARAMETER` blocks are tuned per model, so reusing another family's would sample the model wrongly and skew any bakeoff run against it. To run a new model, add `<family>.Modelfile`.

| Family | `PARAMETER` block |
| --- | --- |
| [`qwen3.5`](modelfiles/qwen3.5.Modelfile) | Qwen's thinking-mode preset: `temperature 1.0`, `top_p 0.95`, `top_k 20`, `min_p 0.0`, `presence_penalty 1.5`, `repeat_penalty 1.0`, `num_predict 8192` - overridden per request by its profile (below) |
| [`qwen3`](modelfiles/qwen3.Modelfile) | Qwen3's thinking-mode preset: `temperature 0.6`, `top_p 0.95`, `top_k 20`, `min_p 0.0`, `repeat_penalty 1.0` |
| [`gemma4`](modelfiles/gemma4.Modelfile) | Gemma's shipped defaults: `temperature 1.0`, `top_p 0.95`, `top_k 64` |
| [`granite4.2`](modelfiles/granite4.2.Modelfile) | Tuned for tool calls: `temperature 0.2`, `top_p 0.9`, `top_k 40`, `min_p 0.05`, `repeat_penalty 1.0`, `num_ctx 16384`, `num_predict 8192` |
| [`llama3.1`](modelfiles/llama3.1.Modelfile) | Meta's reference values: `temperature 0.6`, `top_p 0.9` |
| [`mistral-nemo`](modelfiles/mistral-nemo.Modelfile) | Mistral's low-temperature guidance for Nemo: `temperature 0.3`, `top_p 0.9` |
| [`lfm2.5-thinking`](modelfiles/lfm2.5-thinking.Modelfile) | Liquid's shipped defaults: `temperature 0.05`, `top_k 50` |

Only Qwen3.5 and Granite4.2 are supported. The other five files above retain legacy prompts and are explicitly disabled in `profiles.yaml` until their prompt and thinking settings are ported and tested. Edit `system.md` to change both supported families' assistant prompt. The golden rendering tests preserve their complete Modelfiles at `plan-executor` commit `80171de`; intentional prompt changes must update those baselines.

**Profiles.** What a Modelfile can't express lives in [`modelfiles/profiles.yaml`](modelfiles/profiles.yaml), per family ([`model_profile.py`](src/core/model_profile.py)):

| Family | Thinking levels `off` / `low` / `high` as Ollama's `think` | Runs at | Sampling sent with each request |
| --- | --- | --- | --- |
| `granite4.2` | `false` / `"low"` / `"high"` | `low` (the quick suite passed 8/8 at low in 17 s a turn, against 47 s at high, 2026-09-29) | none - the Modelfile's |
| `qwen3.5` | `false` / `true` / `true` - it has no levels | `off` (on, it thought 13 s over "what is 2+3") | thinking: `temperature 0.6`, `top_p 0.95`; not: `temperature 0.7`, `top_p 0.8`; both `top_k 20`, no presence penalty - a starting point, not yet measured |

A missing or unsupported profile fails with a named error instead of sending an unverified thinking value. The derived `MODEL` tag (including its implicit `:latest`) inherits `NURU_BASE_MODEL`'s profile. The decision layer's thinking tiers (`assess_effort`, when enforced) use the same translation. Evals pick a level per run with `run_suite.py --tiers`.

`core.providers.ollama.create_model(model, base_model, modelfile_path)` resolves the shared SYSTEM text and `__BASE_MODEL__`, validates tool references, and runs `ollama create` with a temporary rendered file. `Session.start()` calls this on every startup before connecting to any MCP server. Eval manifests include the rendered Modelfile's hash, so edits to the shared source change the recorded prompt identity.

Install dependencies and run the CLI:

```bash
uv sync
uv run nuru-cli
```

(`nuru-cli` is what `nuru` used to be called.)

## Claude as the main model

`NURU_MAIN_MODEL=anthropic:claude-haiku-5-5` (or `anthropic:claude-sonnet-5-5`) in `.env.nuru` runs Nuru's turns on Claude instead of Ollama, through the shared [claude-access](../packages/claude-access) package - the same Claude code deep-think's tools use. Unset, or `ollama`, keeps the local model. Everything else is unchanged: the same tools, dispatch, write checks, step cap, conversation store, `nuru-serve` and plugin. Only the model call moves ([`providers/claude.py`](src/core/providers/claude.py)):

- **No Modelfile.** The same [`system.md`](modelfiles/system.md) goes out as the request's system prompt; it and the tool list are marked for prompt caching, so after the first call they're read from the cache.
- **Thinking** is adaptive, steered by `effort`: Nuru's `off` turns it off, `low`/`high` set the effort. The model's thinking blocks are kept on its tool-call messages for the rest of the turn, as the API requires (`NURU_KEEP_THINKING` doesn't apply). Sampling settings aren't sent: these models reject them.
- **Context**: the models take 1M tokens; Nuru still caps what it uses at `NURU_MAX_NUM_CTX`.
- **Ollama** still runs, for the notes search's embeddings only; Nuru builds no `nuru` model and leaves Ollama's loaded models alone when it exits.
- **Key**: `ANTHROPIC_API_KEY`, else `CLAUDE_API_KEY` from `.env.deep-think` - the expert's key.
- **What leaves the machine**: everything the model reads - the system prompt, your messages and every note text a tool returns. Each request is also logged whole, locally, to `nuru/.logs/claude-requests-<date>.jsonl` with its token usage. Rules to keep some notes local are a TODO (R25); for now Claude is treated as a local model.
- **Evals**: `run_suite.py` needs `--allow-cloud` with a Claude main model, and every round ends with a token summary (in, cached, out; per test too, also in the batch's `tokens.json`).

## What happens on each message

1. **Server check** - if Ollama's server isn't already running on `localhost:11434`, it's started in the background (`ollama serve`).
2. **Model check** - if the configured model isn't already loaded into memory, it's loaded, and its params (size, quantization, family, thinking support) are printed.
3. **History loaded** - complete prior user/reply exchanges are read from the local SQLite database (`.db/conversation.db` by default). Stored `content_tokens` estimate each message's content size separately from `tokens_used`, which preserves actual model usage for the CLI context indicator. Existing databases receive an atomic, versioned backfill on opening. History selection scans at most 200 rows in stable ID order and skips orphan messages; empty cancelled replies still complete a turn. Each request reserves 20% for output, capped at 2,048 tokens, and budgets the actual SYSTEM prompt, tools or format schema, timestamp, framing, current message and evidence. The current message is counted once, and its saved copy stays as typed. Continuations evict complete old exchanges, then replace the largest tool-result bodies with explicit omission notices until the request fits. Tool-call relationships and the step-limit final-answer instruction are preserved. A request that still cannot fit after history and optional tool evidence are removed stops with an explicit limitation. The SYSTEM prompt is cached from the exact model-build rendering at session startup. Estimates use UTF-8 bytes divided by four, rounded up; they are not tokenizer guarantees. One bounded tool-result overflow recovery remains for server-side rejection. The `[user: NN%]` prompt continues to show measured prompt usage from the last turn.
4. **Response streamed** - the reply streams in live, prefixed with `[nuru] ` before the first chunk. If the model calls a tool (from the Obsidian or math MCP servers - see their own READMEs under [servers/](../servers)), the tool runs and its result is fed back to the model before the final reply is produced. The model is only offered the tools on an allowlist (`MODEL_TOOLS` in [`tool_contract.py`](src/core/tool_contract.py)): the vault (where a delete is a move to `trash/`), `calculate`, `statistics`, optional Docker-backed `run_python`, and `ask_an_expert` - never the decision layer's own tools. What a thinking model reasoned is printed as `[thinking] ` once each model call is done. If it ends a turn with reasoning but no reply (it sometimes writes its whole answer into the reasoning), the reply is a one-line note saying so, and the reasoning is saved as the reply.

History is scoped to one conversation per run of `nuru` - each launch starts a new conversation row in the database, but earlier conversations are kept, not deleted.

5. **Decision layer** - at every decision point the harness also asks [Laya](../servers/laya) what it would do - what kind of request this is, whether to go on after a tool result, how much thinking the next step needs, whether a write should go ahead, whether the reply matches what actually ran - and logs that next to what Nuru actually did, in `.db/decisions.sqlite3`. Every tool Nuru runs is logged there too, from what actually ran (note text in a write is kept as its length and hash, not the text).

   Each decision point is `off` (not asked), `shadow` (logged, changes nothing - if the Laya server is down or still loading, the turn runs exactly the same) or `enforce`, set per point in [`config/decision_layer.yaml`](../config/decision_layer.yaml). A point that asks Laya can't be enforced until its thresholds are calibrated on held-out labelled data; a point decided by code alone can. **Enforced today** (since 2026-09-30):
   - **watch_thinking** - the thinking watch's code checks ([`thinking_watch.py`](src/core/thinking_watch.py)): a call past its thinking budget (low 1,200 tokens) or repeating a sentence 3 times is stopped, and the model asked to act with thinking off for that one call; the same tool called with the same arguments (ignoring case) 3 times in a turn stops the turn.
   - **step_cap** - a turn stops after 10 rounds of tool calls (after one expert attempt if the budget allows), with the log of what ran.
   - **write_checks** - a write naming a note ID no search returned, a blocked tool or a protected path doesn't run; the model is told why.

   **Off:** `assess_effort` (Laya's thinking tier): every call thinks at its model's default level (its [profile](#the-custom-model-and-its-settings)). The code stays for when Laya is trained. **Shadow:** `tool_gate`, `gate_write`, `check_claim`, `rerank` and `watch_laya` (the watch's Laya question). Enforced later ([`enforcement.py`](src/core/enforcement.py)):
   - **tool_gate** - a read or write request starts with the harness running the search itself (the request as its query); `answer` / `ask_user` get no tools.
   - **assess_effort** - every model call's thinking level comes from the tier policy ([`tier_policy.py`](src/core/tier_policy.py)): only up within a turn, one tier up on a failure signal (a `cannot_do`, a schema failing twice, an empty reply, a failed claim check), and past `high` the expert plans ([`expert.py`](src/core/expert.py) - it sees the request, the tools, note titles and IDs, never note contents; key from `ANTHROPIC_API_KEY`, capped per day). Without the expert, the turn stops and says why.
   - **gate_write** - a write's arguments are asked for again under a forced schema whose note IDs can only be ones the last search returned ([`write_args.py`](src/core/write_args.py)), validated, then gate_write decides: go ahead, ask you (`[confirm] Run ...? [y/N]` in the CLI; the UI has no prompt yet, so there it's blocked), or block.
   - **check_claim** - the reply is held until checked against what ran; a false claim is written again once, then shown with a warning.

## Reviewing the decision layer

```bash
uv run nuru label                  # label logged decisions, disagreements with what Nuru did first
uv run nuru label --point gate_write --limit 10
uv run nuru export labels.jsonl    # the labelled decisions, for calibrating and fine-tuning later
```

In the CLI, an enforced gate_write that wants your OK asks before the write runs.

`label` shows each decision - what Laya proposed, what Nuru did, the state Laya saw - and asks for the right answer to each of its questions (a number or a name; Enter skips one, `q` stops). Labels are recorded as coming from `user`, or `--source claude`; never from the local model.

## Commands

Anything you type that isn't one of the commands below is sent to the model as a prompt (which also loads the model first if it isn't loaded yet).

- **`/help`** - Print the list of available commands.
- **`/load`** - Load the model into memory (starting the server first if needed) without sending a prompt.
- **`/show`** - Print the extracted model info (`parameter_size`, `quantization_level`, `family`, `supports_thinking`, `context_window`, `embedding_length`) - the same fields recorded in the logs.
- **`/exit`, `/bye`, `/end`, `/quit`** - Unload all models, stop the Ollama server (if this process started it), and exit.

## Exiting

Ctrl-D (EOF) exits the loop without unloading the model or stopping the server - use `/exit` (or one of its aliases) for a clean shutdown. Ctrl-C is not currently handled and will raise a `KeyboardInterrupt` traceback.

## Module reference

`core.session` - what the CLI and `nuru-serve` share, with no dependency on either:

- **`Session.start()`** - Builds the custom model, connects the MCP servers, opens the log and the database, loads the model, and returns the `Session`. If a later step fails, whatever was already opened is closed again.
- **`session.ask(text, cancel=None)`** - A generator for one turn. Yields `Text` chunks of the reply, a `Thinking` for what each model call reasoned (once that call is done, so it follows the text it was reasoning towards) and a `ToolCall` for each tool the model calls (before it runs), then a `Failure` if the turn couldn't be completed (an oversized message, or a failed request - a failed initial admission is not saved; a context-budget failure after a turn starts preserves its request and limitation). Setting the `threading.Event` passed as `cancel` stops the turn at the next chunk, or once a running tool call returns, and keeps what was said so far as the reply.
- **`session.load_model()`** / **`loaded_model_info()`** - Load the model if needed; its static info, or `None` if it isn't in memory.
- **`session.shutdown(reason)`** - Unload all models and stop the Ollama server if this process started it, and log why.
- **`session.close()`** - Release the MCP servers and the database, leaving the model loaded and Ollama running.

`core.decision_shadow` - `DecisionShadow`, the session's hooks into the decision layer: `begin` (tool_gate and assess_effort before the first model call), `model_called` (what the model did, recorded against those decisions), `before_tool` / `after_tool` (gate_write for a write, the execution log, rerank for `semantic_search`), `before_next_call` and `end` (check_claim). Any failure in it is counted in `failures` and swallowed.

`core.enforcement`, `core.tier_policy`, `core.expert`, `core.write_args`, `core.forced_schema` - the enforce paths above, each kept separate from the session loop so it can be read and tested on its own.

`core.label_cli` - the `nuru label` / `nuru export` commands above.

`core.model_profile` - [profiles](#the-custom-model-and-its-settings): a family's thinking translation, default level and sampling, from `modelfiles/profiles.yaml`.

`core.providers.ollama` - a thin client for Ollama's REST API, with no dependency on the CLI:

- **`create_model(model, base_model, modelfile_path)`** - Renders `modelfile_path`'s `__BASE_MODEL__` placeholder to `base_model` and runs `ollama create model -f <rendered file>`. Safe to call every startup - `ollama create` overwrites an existing model of the same name.
- **`is_server_running()`** - Whether Ollama's server responds on `localhost:11434`.
- **`ensure_server_running(timeout=15)`** - Starts `ollama serve` in the background if it isn't already up, and waits (up to `timeout` seconds) for it to respond. Raises `RuntimeError` if it never comes up.
- **`loaded_models()`** - Models currently resident in memory (via `/api/ps`).
- **`model_info(model)`** - Static info about a model - architecture, parameter size, quantization, capabilities (via `/api/show`).
- **`ensure_model_loaded(model)`** - Loads `model` into memory if it isn't resident yet, then prints a one-line summary from `model_info` and returns a dict with `parameter_size`, `quantization_level`, `family`, `supports_thinking`, `context_window`, and `embedding_length`.
- **`chat(model, messages, num_ctx=8192, think=True)`** - A generator that yields streamed response text chunks (`str`), then `{"thinking": ...}` if the model reasoned, then a final usage dict (`{"prompt_tokens", "completion_tokens", "stop_reason"}`). Also prints each outgoing message as `[role] content` before sending.

## Testing

```bash
uv run pytest
```

No live Ollama server is required - the tests fake out `requests` so they run standalone. See the [servers README](../servers/README.md) for the pattern this repo generally follows for organizing and testing standalone apps like this one.


## Validated tool dispatch and uncertain writes

Every model-facing tool call is checked against connected tools and their advertised
JSON Schema before execution. Arguments must be JSON objects. Python accepts only
`code` and `notes`; Nuru injects note text and validates the resulting server request.
Tool errors carry a common kind, code, hint and completion certainty in logs and model
results. Math/Python domain failures override a successful protocol flag; note content
is not interpreted as a tool-status envelope. Recoverable failures get two recovery calls;
failed evidence cannot feed a dependent write.

A write attempt is committed to the conversation database before dispatch. If its
response is lost or reports an error without an explicit pre-mutation rejection
receipt, Nuru cannot assume nothing changed: it preserves
the request, reports uncertainty and blocks further writes to that vault/database.
This holds with Laya unavailable and after reopening. Reads remain available. A model
failure after a confirmed write reports that completed action instead of deleting the
request and inviting an indistinguishable retry.

After stopping other sessions on the vault and inspecting its actual state, use
`/writes` in the CLI to inspect pending attempts, then `/resolve-write ID completed`
or `/resolve-write ID not_completed` to record a verified conclusion. Neither command
executes a write; the model cannot resolve attempts. Leave uncertainty unresolved if
you cannot establish what happened. Separate conversation databases do not share the
journal; canonical authorization and target provenance remain the next work package.
See [PR 3 findings](evals/validated-dispatch-pr3-2026-10-06.md) for coverage and limits.

Pre-write refusals from the pinned Obsidian server carry an explicit
`REJECTED_BEFORE_WRITE` receipt. These can be corrected without locking the vault;
exception type names alone never make a dispatched write safe to retry. Recovery
allows two calls per turn across reads and computations, while failed evidence
continues to block dependent writes. Planned steps permit two bounded corrections.

The desktop **Pending writes** button shows unresolved attempts while idle. Inspect
the affected notes and move destinations, stop other sessions using that vault, then
acknowledge those checks to mark an attempt completed or not completed. Leave it
pending if unsure. These actions record a conclusion and do not retry any write.
