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
MODEL=qwen3:8b
THINK=true
```

- `MODEL` - which Ollama model to chat with.
- `THINK` - whether to ask the model to use its reasoning/thinking mode, for models that support it (e.g. `qwen3`, `gemma4`). Ignored by models that don't. Any value other than `false` counts as enabled.

Install dependencies and run:

```bash
uv sync
uv run nuru
```

## What happens on each message

1. **Server check** - if Ollama's server isn't already running on `localhost:11434`, it's started in the background (`ollama serve`).
2. **Model check** - if the configured model isn't already loaded into memory, it's loaded, and its params (size, quantization, family, thinking support) are printed.
3. **Prompt echoed** - the message you sent is printed as `[user] ...` before the request goes out.
4. **Response streamed** - the reply streams in live, prefixed with `[nuru] ` before the first chunk.

Each message is sent as an independent, single-turn conversation - there's no chat history carried across turns.

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
