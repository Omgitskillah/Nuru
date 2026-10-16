# deep-think-mcp

An MCP server whose tools call Claude through the shared [claude-access](../../packages/claude-access) provider contract. The provider answering each call is picked by `DEEP_THINK_PROVIDER` (see `providers/__init__.py`), defaulting to `claude`, the live Anthropic API.

Its one tool is **`write_plan`**, for a harness (Nuru's live planner in the evals), not a model: given the user's `request`, the planner's `instructions`, an optional JSON `schema` and a `model` (`claude-haiku-4-5` or `claude-sonnet-5-5`, each at its lowest thinking setting), it returns `{plan, stop_reason, model}` - only the plan text, never the thinking.

`ask_an_expert` was removed on 2026-10-16: it had been hidden from the model since 2026-10-05 and was no longer used. Its file attachments and upload cache went with it.

## Configuration

deep-think's settings live in `.env.deep-think` in the repo root (see [Settings](../../README.md#settings)). From the root, `cp .env.deep-think.example .env.deep-think` and set:

- `DEEP_THINK_PROVIDER` - which provider to dispatch to (`claude` is the default).
- `CLAUDE_API_KEY` - a real `sk-ant-...` key, required for the `claude` provider to reach the live API.

To add a provider, implement `claude_access.contract.Provider` and register it in `providers/__init__.py`'s `_REGISTRY`.

## Running

```bash
cd servers/deep-think
uv run deep-think-mcp
```

See the top-level [servers/README.md](../README.md) for running tests and using the MCP Inspector.
