# nuru

A terminal chat CLI backed by a local [Ollama](https://ollama.com) model. Type a line, hit enter, and it's sent straight to the model; the reply streams back live, prefixed with `[nuru]`.

## Setup

Requires [Ollama](https://ollama.com) installed (`ollama` on your `PATH`) and at least one model pulled, e.g.:

```bash
ollama pull qwen3:8b
```

Copy `.env.example` to `.env` and adjust if you want a different default model:

```bash
cp .env.example .env
```

```
NURU_BASE_MODEL=qwen3:8b
MODEL=nuru
THINK=true
```

- `NURU_BASE_MODEL` - the plain Ollama model to build Nuru's custom model from (must already be pulled).
- `MODEL` - the model actually loaded and chatted with. On every startup, `main()` runs `ollama create $MODEL -f Modelfile` from `NURU_BASE_MODEL`, so `MODEL` should be a distinct, derived tag (default `nuru`) rather than `NURU_BASE_MODEL`'s own name - setting them to the same value makes `ollama create` try to rebuild a model from itself.
- `THINK` - whether to ask the model to use its reasoning/thinking mode, for models that support it (e.g. `qwen3`, `gemma4`). Ignored by models that don't. Any value other than `false` counts as enabled.

## The custom model and its system prompt

[`Modelfile`](Modelfile) is a template (`FROM __BASE_MODEL__` plus a `SYSTEM` prompt) that tells the model it's Nuru, a personal assistant with tool access, and spells out when to use the Obsidian vault tools directly versus when to escalate to `ask_an_expert` (deep-think's tool for offloading long, careful reasoning to a cloud model) - and to be frugal with `ask_an_expert` calls specifically, since they're slower and costlier than answering directly.

`core.providers.ollama.create_model(model, base_model, modelfile_path)` renders `__BASE_MODEL__` to `NURU_BASE_MODEL` and runs `ollama create` with the result. `main()` calls this on every startup before connecting to any MCP server, so the system prompt is always in sync with the template - edit `Modelfile` and it takes effect on the next run, no manual `ollama create` needed.

Install dependencies and run:

```bash
uv sync
uv run nuru
```

## What happens on each message

1. **Server check** - if Ollama's server isn't already running on `localhost:11434`, it's started in the background (`ollama serve`).
2. **Model check** - if the configured model isn't already loaded into memory, it's loaded, and its params (size, quantization, family, thinking support) are printed.
3. **History loaded** - prior turns from the current conversation are read back from a local SQLite database (`db/conversation.db` by default, via `core.conversation_store.ConversationStore`) and sent along with the new message, oldest first, keeping as many of the most recent turns as fit under a token budget (80% of the model's context window - the rest is reserved for the response). A message that would already exceed that budget on its own is rejected with an error instead of being sent. The `[user: NN%]` prompt shows how much of the context window the last turn used.
4. **Response streamed** - the reply streams in live, prefixed with `[nuru] ` before the first chunk. If the model calls a tool (from the Obsidian or deep-think MCP servers - see their own READMEs under [servers/](../servers)), the tool runs and its result is fed back to the model before the final reply is produced.

History is scoped to one conversation per run of `nuru` - each launch starts a new conversation row in the database, but earlier conversations are kept, not deleted.

## Commands

Anything you type that isn't one of the commands below is sent to the model as a prompt (which also loads the model first if it isn't loaded yet).

- **`/help`** - Print the list of available commands.
- **`/load`** - Load the model into memory (starting the server first if needed) without sending a prompt.
- **`/show`** - Print the extracted model info (`parameter_size`, `quantization_level`, `family`, `supports_thinking`, `context_window`, `embedding_length`) - the same fields recorded in the logs.
- **`/exit`, `/bye`, `/end`, `/quit`** - Unload all models, stop the Ollama server (if this process started it), and exit.

## Exiting

Ctrl-D (EOF) exits the loop without unloading the model or stopping the server - use `/exit` (or one of its aliases) for a clean shutdown. Ctrl-C is not currently handled and will raise a `KeyboardInterrupt` traceback.

## Module reference

`core.providers.ollama` - a thin client for Ollama's REST API, with no dependency on the CLI:

- **`create_model(model, base_model, modelfile_path)`** - Renders `modelfile_path`'s `__BASE_MODEL__` placeholder to `base_model` and runs `ollama create model -f <rendered file>`. Safe to call every startup - `ollama create` overwrites an existing model of the same name.
- **`is_server_running()`** - Whether Ollama's server responds on `localhost:11434`.
- **`ensure_server_running(timeout=15)`** - Starts `ollama serve` in the background if it isn't already up, and waits (up to `timeout` seconds) for it to respond. Raises `RuntimeError` if it never comes up.
- **`loaded_models()`** - Models currently resident in memory (via `/api/ps`).
- **`model_info(model)`** - Static info about a model - architecture, parameter size, quantization, capabilities (via `/api/show`).
- **`ensure_model_loaded(model)`** - Loads `model` into memory if it isn't resident yet, then prints a one-line summary from `model_info` and returns a dict with `parameter_size`, `quantization_level`, `family`, `supports_thinking`, `context_window`, and `embedding_length`.
- **`chat(model, messages, num_ctx=8192, think=True)`** - A generator that yields streamed response text chunks (`str`), then a final usage dict (`{"prompt_tokens", "completion_tokens", "stop_reason"}`). Also prints each outgoing message as `[role] content` before sending.

## Testing

```bash
uv run pytest
```

No live Ollama server is required - the tests fake out `requests` so they run standalone. See the [servers README](../servers/README.md) for the pattern this repo generally follows for organizing and testing standalone apps like this one.
