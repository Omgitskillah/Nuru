"""Shared access to Claude for every part of Nuru that calls it.

- `contract.Provider`: the API contract (messages, streamed messages, token counting, models).
- `claude.ClaudeProvider`: that contract against the real Anthropic API.
- `keys.api_key()`: the one place the API key is read from.
- `models.MAIN_MODELS`: the Claude models Nuru can run as its main model.
"""
