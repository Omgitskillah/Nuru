# nuru

A chat app backed by a local [Ollama](https://ollama.com) model, as a terminal CLI (`nuru-cli`) or a desktop window (`nuru-ui`). Type a line, hit enter, and it's sent straight to the model; the reply streams back live - prefixed with `[nuru]` in the CLI, drawn as markdown in the UI.

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
- `MODEL` - the model actually loaded and chatted with. On every startup, `Session.start()` runs `ollama create $MODEL -f Modelfile` from `NURU_BASE_MODEL`, so `MODEL` should be a distinct, derived tag (default `nuru`) rather than `NURU_BASE_MODEL`'s own name - setting them to the same value makes `ollama create` try to rebuild a model from itself.
- `THINK` - whether to ask the model to use its reasoning/thinking mode, for models that support it (e.g. `qwen3`, `gemma4`). Ignored by models that don't. Any value other than `false` counts as enabled.

## The custom model and its system prompt

[`Modelfile`](Modelfile) is a template (`FROM __BASE_MODEL__` plus a `SYSTEM` prompt) that tells the model it's Nuru, a personal assistant with tool access, and spells out when to use the Obsidian vault tools directly versus when to escalate to `ask_an_expert` (deep-think's tool for offloading long, careful reasoning to a cloud model) - and to be frugal with `ask_an_expert` calls specifically, since they're slower and costlier than answering directly.

`core.providers.ollama.create_model(model, base_model, modelfile_path)` renders `__BASE_MODEL__` to `NURU_BASE_MODEL` and runs `ollama create` with the result. `Session.start()` calls this on every startup before connecting to any MCP server, so the system prompt is always in sync with the template - edit `Modelfile` and it takes effect on the next run, no manual `ollama create` needed.

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
3. **History loaded** - prior turns from the current conversation are read back from a local SQLite database (`db/conversation.db` by default, via `core.conversation_store.ConversationStore`) and sent along with the new message, oldest first, keeping as many of the most recent turns as fit under a token budget (80% of the model's context window - the rest is reserved for the response). The new message goes to the model behind a line giving the current local date and time (`[Current date and time: Monday 2026-09-21 17:52 CEST]`); the saved copy stays as you typed it. A message that would already exceed that budget on its own is rejected with an error instead of being sent. The `[user: NN%]` prompt shows how much of the context window the last turn used.
4. **Response streamed** - the reply streams in live, prefixed with `[nuru] ` before the first chunk. If the model calls a tool (from the Obsidian or deep-think MCP servers - see their own READMEs under [servers/](../servers)), the tool runs and its result is fed back to the model before the final reply is produced. What a thinking model reasoned is printed as `[thinking] ` once each model call is done. If it ends a turn with reasoning but no reply (it sometimes writes its whole answer into the reasoning), the reply is a one-line note saying so, and the reasoning is saved as the reply.

History is scoped to one conversation per run of `nuru` - each launch starts a new conversation row in the database, but earlier conversations are kept, not deleted.

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
