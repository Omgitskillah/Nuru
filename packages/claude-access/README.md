# claude-access

Shared access to Claude for everything in this repo that calls it:

- **deep-think** (`servers/deep-think`) - its `write_plan` tool;
- **Nuru's main model** (`nuru/src/core/providers/claude.py`, `NURU_MAIN_MODEL=anthropic:<id>`);
- **Nuru's expert planner** (`nuru/src/core/expert.py`) - its client.

| Module | What it is |
|---|---|
| `contract.py` | `Provider`: `send_message`, `stream_message` (streamed: text and thinking as they arrive, then the whole message), `count_tokens`, `list_models` |
| `claude.py` | `ClaudeProvider`, the contract against the real API through the `anthropic` SDK; `build_client()`. A `fallbacks` value goes through the beta endpoint (server-side refusal fallback) |
| `keys.py` | `api_key()`: `ANTHROPIC_API_KEY`, else `CLAUDE_API_KEY`, else `CLAUDE_API_KEY` in the repo root's `.env.deep-think` |
| `models.py` | `MAIN_MODELS`: the models Nuru can run as its main model (Claude Haiku 5.5, Claude Sonnet 5.5) |

Tests (`tests/`) never reach the network: `ClaudeProvider` takes an injected client, and the tests pass a mock.
