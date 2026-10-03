# nuru

A chat app backed by a local [Ollama](https://ollama.com) model, as a terminal CLI (`nuru-cli`) or a desktop window (`nuru-ui`). Type a line, hit enter, and it's sent straight to the model; the reply streams back live - prefixed with `[nuru]` in the CLI, drawn as markdown in the UI.

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
- `NURU_PLANNER_MODEL` / `NURU_PLANNER_THINK` - the model that writes plans in [plan mode](#plan-mode-a-test), and whether it thinks. Default: `MODEL`, thinking off.

Switching models is changing `NURU_BASE_MODEL`: everything else that has to change with it comes from the family's files (see [the model's settings](#the-custom-model-and-its-settings)), including how much it thinks - there is no `THINK` setting any more.

## The custom model and its settings

[`modelfiles/`](modelfiles) holds one Modelfile per model family, each a template (`FROM __BASE_MODEL__`, a `PARAMETER` block, and a `SYSTEM` prompt) that tells the model it's Nuru, a personal assistant with tool access, and spells out when to use the Obsidian vault tools directly versus when to escalate to `ask_an_expert` (deep-think's tool for offloading long, careful reasoning to a cloud model) - and to be frugal with `ask_an_expert` calls specifically, since they're slower and costlier than answering directly.

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

Granite and Qwen3.5 carry the current `SYSTEM` prompt (the 10 tools since the 2026-10 tools purge); the other families' files still describe the old tools until they're ported. The copies are separate, so a prompt change has to be made in each, or they drift - which would confound a bakeoff comparing them.

**Profiles.** What a Modelfile can't express lives in [`modelfiles/profiles.yaml`](modelfiles/profiles.yaml), per family ([`model_profile.py`](src/core/model_profile.py)):

| Family | Thinking levels `off` / `low` / `high` as Ollama's `think` | Runs at | Sampling sent with each request |
| --- | --- | --- | --- |
| `granite4.2` | `false` / `"low"` / `"high"` | `low` (the quick suite passed 8/8 at low in 17 s a turn, against 47 s at high, 2026-09-29) | none - the Modelfile's |
| `qwen3.5` | `false` / `true` / `true` - it has no levels | `off` (on, it thought 13 s over "what is 2+3") | thinking: `temperature 0.6`, `top_p 0.95`; not: `temperature 0.7`, `top_p 0.8`; both `top_k 20`, no presence penalty - a starting point, not yet measured |

A family without a profile runs at `low`, passed to Ollama as written. The decision layer's thinking tiers (`assess_effort`, when enforced) use the same translation. Evals pick a level per run with `run_suite.py --tiers`.

`core.providers.ollama.create_model(model, base_model, modelfile_path)` renders `__BASE_MODEL__` to `NURU_BASE_MODEL` and runs `ollama create` with the result. `Session.start()` calls this on every startup before connecting to any MCP server, so the system prompt is always in sync with the template - edit the family's Modelfile and it takes effect on the next run, no manual `ollama create` needed.

Install dependencies and run either front end:

```bash
uv sync
uv run nuru-cli   # in the terminal
uv run nuru-ui    # in a window
```

(`nuru-cli` is what `nuru` used to be called.)

## What happens on each message

1. **Server check** - if Ollama's server isn't already running on `localhost:11434`, it's started in the background (`ollama serve`).
2. **Model check** - if the configured model isn't already loaded into memory, it's loaded, and its params (size, quantization, family, thinking support) are printed.
3. **History loaded** - prior turns from the current conversation are read back from a local SQLite database (the hidden `.db/conversation.db` by default, via `core.conversation_store.ConversationStore`) and sent along with the new message, oldest first, keeping as many of the most recent turns as fit under a token budget (80% of the model's context window - the rest is reserved for the response). The new message goes to the model behind a line giving the current local date and time (`[Current date and time: Monday 2026-09-21 17:52 CEST]`); the saved copy stays as you typed it. A message that would already exceed that budget on its own is rejected with an error instead of being sent. The `[user: NN%]` prompt shows how much of the context window the last turn used.
4. **Response streamed** - the reply streams in live, prefixed with `[nuru] ` before the first chunk. If the model calls a tool (from the Obsidian or deep-think MCP servers - see their own READMEs under [servers/](../servers)), the tool runs and its result is fed back to the model before the final reply is produced. The model is only offered the tools on an allowlist (`MODEL_TOOLS` in [`session.py`](src/core/session.py)): the vault (where a delete is a move to `trash/`), `calculate`, `statistics` and `ask_an_expert` - never the decision layer's own tools. What a thinking model reasoned is printed as `[thinking] ` once each model call is done. If it ends a turn with reasoning but no reply (it sometimes writes its whole answer into the reasoning), the reply is a one-line note saying so, and the reasoning is saved as the reply.

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

   Always on, in code: `ask_an_expert` answers at most twice a turn. The second answer tells the model it was the last, and to ask you what's unclear if the answers don't settle it; the tool is then no longer offered, and a call made anyway ends the turn with that question to you, and its `file_paths` are resolved to real vault paths first, so a note named by its title attaches.

## Plan mode (a test)

A message starting with `PLAN:` gets a plan back instead of an answer: the steps that would carry the request out, as JSON. Nothing in it runs. It is the first half of a plan-then-act structure, where the harness runs the tool steps and the model only fills in narrow `pick` / `draft` steps; the second half (the executor) isn't built.

- **Format** - `{"steps": [...]}`; a tool step is `{"step", "tool", "with"}`, a think step `{"step", "think": "pick" | "draft", "uses", "want"}`, and `"$sN"` in an argument stands for step sN's output. The harness adds the final `answer` step itself.
- **How it's written** ([`planner.py`](src/core/planner.py)) - one call to `NURU_PLANNER_MODEL` with no tools offered and Ollama's `format` forcing the plan's schema, so the model can only plan. Code then checks what the schema can't (references point back, each tool gets its own arguments, `calculate` gets arithmetic), and a plan with problems goes back to the model once. The reply is the plan plus anything code still found wrong.
- **Which model** - see [`evals/plan-mode-check-2026-10-03.md`](evals/plan-mode-check-2026-10-03.md). On its test prompt `qwen3:8b` and `mistral-nemo:12b` planned correctly 5/5 and `qwen3.5:4b` 2/5; Granite 3B, and every other model under 8B, 0/5. Asked to plan inside the normal turn, with tools offered, Granite calls them instead.
- Ollama writes a `format` schema's keys in alphabetical order, whatever order the schema gives, so the key names are chosen to sort into reading order.

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

## Desktop UI

`uv run nuru-ui` opens the same chat in a window (PyQt6). It runs on the same `core.session` as the CLI - same model, tools, history and logging - so only the display differs.

- **One bubble per reply.** Where the CLI prints `[nuru]`, `[thinking]` and `[tool]` labels, the UI keeps a whole turn in a single reply bubble and separates its pieces with markdown breaks (`---`). Your own message stays in its own bubble.
- **Nuru's working folds away.** A small card above each reply says "Thinking…" while it streams, with its working listed under it as it happens: the tool calls first, kept together however they arrived - `tool: ` then the name and arguments, cut off at 80 characters - and below them `thinking: ` and what the model reasoned, wrapped in full. When the reply lands the card shuts itself and says what was done ("Used 2 tools", or "Thought about it" when no tool was called); clicking the line opens it again. A reply with no working behind it loses the card altogether.
- **Starting up.** The window opens straight away with the lantern rocking beside "Starting Nuru…" and no input box. Building the model, connecting the MCP servers and loading the model happen in the background; when they finish, the lantern settles, the greeting replaces the loading text, and the input box appears. If that fails, the reason is shown in the window and the input stays off.
- **Stop.** The send button becomes a stop button while a reply streams. Stopping keeps what had been said so far as the reply, both in the window and in the saved history.
- **Closing the window** is `/exit`: it unloads all models and stops the Ollama server if the UI started it.
- There are no slash commands and no context-usage indicator; those are CLI only.

The app icon is the same lantern, centred on a white tile shaped the way macOS shapes app icons - a rounded square filling 824 of the 1024-point canvas - so it doesn't disappear into a dark Dock.

Links in a reply only open if they go to a web page (`http`, `https`) or a mail client (`mailto`).

## Module reference

`core.session` - what the CLI and the UI share, with no dependency on either:

- **`Session.start()`** - Builds the custom model, connects the MCP servers, opens the log and the database, loads the model, and returns the `Session`. If a later step fails, whatever was already opened is closed again.
- **`session.ask(text, cancel=None)`** - A generator for one turn. Yields `Text` chunks of the reply, a `Thinking` for what each model call reasoned (once that call is done, so it follows the text it was reasoning towards) and a `ToolCall` for each tool the model calls (before it runs), then a `Failure` if the turn couldn't be completed (an oversized message, or a failed request - in which case the message is not saved). Setting the `threading.Event` passed as `cancel` stops the turn at the next chunk, or once a running tool call returns, and keeps what was said so far as the reply.
- **`session.load_model()`** / **`loaded_model_info()`** - Load the model if needed; its static info, or `None` if it isn't in memory.
- **`session.shutdown(reason)`** - Unload all models and stop the Ollama server if this process started it, and log why.
- **`session.close()`** - Release the MCP servers and the database, leaving the model loaded and Ollama running.

`core.decision_shadow` - `DecisionShadow`, the session's hooks into the decision layer: `begin` (tool_gate and assess_effort before the first model call), `model_called` (what the model did, recorded against those decisions), `before_tool` / `after_tool` (gate_write for a write, the execution log, rerank for `semantic_search`), `before_next_call` and `end` (check_claim). Any failure in it is counted in `failures` and swallowed.

`core.enforcement`, `core.tier_policy`, `core.expert`, `core.write_args`, `core.forced_schema` - the enforce paths above, each kept separate from the session loop so it can be read and tested on its own.

`core.label_cli` - the `nuru label` / `nuru export` commands above.

`core.model_profile` - [profiles](#the-custom-model-and-its-settings): a family's thinking translation, default level and sampling, from `modelfiles/profiles.yaml`.

`core.planner` - [plan mode](#plan-mode-a-test): the planner prompt and schema, `check_plan`, and `make_plan`.

`core.reply_markdown` - Qt-free helpers for the UI: `Reply` folds a turn's events into one markdown document, and `split_fences` separates fenced code from the prose around it.

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

No live Ollama server is required - the tests fake out `requests` so they run standalone. The UI tests run headless on Qt's `offscreen` platform (no display needed); if the machine's Qt libraries can't be loaded, that file is skipped rather than failed. See the [servers README](../servers/README.md) for the pattern this repo generally follows for organizing and testing standalone apps like this one.
