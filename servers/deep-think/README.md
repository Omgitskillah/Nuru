# deep-think-mcp

MCP server that enforces a single API contract (`contract.py`) across
interchangeable backend **providers**, each implementing it against a
different vendor's API. The contract covers the full surface below, but the
only tool exposed to MCP clients is `ask_an_expert` - the rest exist as
`Provider` methods that `ask_an_expert` (and any future tool) can call
internally, without being independently callable over MCP.

| Contract endpoint | `Provider` method |
|---|---|
| `POST /v1/messages` | `send_message` |
| `POST /v1/messages/count_tokens` | `count_tokens` |
| `GET /v1/models` | `list_models` |
| `POST /v1/files` / `GET /v1/files` | `upload_file` / `list_files` |

Whichever method is called dispatches to whichever provider is selected via
the `DEEP_THINK_PROVIDER` env var (see `providers/__init__.py`), defaulting
to `claude`.

`ask_an_expert` is built on top of the contract rather than being a direct
passthrough: given a `question`, an optional `best_guess` (the caller's own draft, for the
expert to correct), optional `file_paths` and an optional `user_request` (the user's original
message verbatim - Nuru fills this in itself rather than have its model copy it), it resolves each
file to a `file_id` via `file_cache.py` (uploading it only if it's new,
changed, or its previous upload expired - see below), attaches them as
`document`/`image` content blocks, and calls the provider's `send_message`
against a fixed model (`server.ASK_AN_EXPERT_MODEL`).

The request is assembled from those parts under labelled headings - the user's request,
the attached files, the best guess, the question - with the files placed ahead of the text,
and a fixed system prompt (`server.EXPERT_SYSTEM_PROMPT`) tells the expert it is advising a
small local assistant that will relay its answer, that it cannot see the vault, and to lead
with an answer for the user in about 250 words. A small model writing the request freehand
sent its finished answer with no question in it, or asked the expert for notes only the user
could supply.

It returns `{answer, stop_reason}`, not the provider's whole response: `answer` is the
expert's text blocks joined, and a `hint` is added when the answer is empty or stopped at
`max_tokens`. The full response carries the expert's thinking (with a long signature per
block) and usage metadata; handed back whole it came to ~12k tokens on one escalation and
pushed a 16k-context local model over its window.

### File cache

`file_cache.py` keeps a SQLite database (`DEEP_THINK_MCP_DB_PATH`, see
below) mapping each local file path to its last-uploaded `file_id`, content
hash, and expiry. `ask_an_expert` re-uploads a file only when its content
hash has changed or its cached entry has expired; otherwise it reuses the
stored `file_id`. Superseded or expired entries are dropped from the cache
but are never explicitly deleted from the provider - they're left to
auto-expire there.

Currently registered providers:

| Provider | `DEEP_THINK_PROVIDER` value | Notes |
|---|---|---|
| Claude | `claude` (default) | **Live** - calls the real Anthropic API via the `anthropic` SDK. Auth comes from `CLAUDE_API_KEY` in `.env` (see `.env.example`); falls back to the SDK's own credential resolution (`ANTHROPIC_API_KEY`, an `ant auth login` profile, etc.) if unset. |

Unit tests never hit the network: `ClaudeProvider` accepts an injected
`client`, and `tests/test_claude_provider.py` passes in a mocked
`anthropic.Anthropic` client instead of a real one.

To add a new provider (OpenAI, Gemini, ...), implement
`deep_think_mcp.contract.Provider` in `providers/<name>.py` and register it
in `providers/__init__.py`'s `_REGISTRY`.

## Configuration

Copy `.env.example` to `.env` and set:

- `DEEP_THINK_PROVIDER` - which provider to dispatch to (`claude` is the default).
- `CLAUDE_API_KEY` - a real `sk-ant-...` key, required for the `claude` provider to reach the live API.
- `DEEP_THINK_MCP_DB_PATH` - path to the SQLite file-cache database used by `ask_an_expert` (defaults to `file_cache.db` next to this package).

## Running

```bash
cd servers/deep-think
uv run deep-think-mcp
```

See the top-level [servers/README.md](../README.md) for running tests and
using the MCP Inspector - the Inspector is also the easiest way to call
these tools by hand from the terminal.
