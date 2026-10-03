# Servers

Each MCP server lives in its own self-contained folder here (own `pyproject.toml`, deps, tests), and they're tied together by a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) rooted at the repo root, so `uv run pytest` from there discovers and runs every package's tests in one go.

The servers: [obsidian](obsidian) (the vault, a git submodule from [obsidian-4-llms](https://github.com/Omgitskillah/obsidian-4-llms)), [deep-think](deep-think) (`ask_an_expert`, the cloud expert), [math-4-llms](math-4-llms) (a git submodule: `calculate` for strict expressions and `statistics` for a list of numbers, so the local model doesn't do sums in its head) and [laya](laya) (Nuru's decision layer: a small local decision model the harness asks before it acts).

## Requirements

- [uv](https://docs.astral.sh/uv/) — Python package/project manager used to run these servers.
- Node.js/npm (for `npx`) — only needed to run the [MCP Inspector](https://github.com/modelcontextprotocol/inspector), a browser-based tool for manually invoking a server's tools during development.

## Running a server

For example, to run the Obsidian server:

```bash
cd servers/obsidian
uv run obsidian-mcp
```

This starts the server over stdio and waits for a host (e.g. Claude Desktop) to connect - it won't print anything on its own.

## Testing with the MCP Inspector

To interactively call a server's tools without connecting a full host:

```bash
cd servers/obsidian
MCP_INSPECTOR_LOG_DIR=./.logs npx @modelcontextprotocol/inspector uv run obsidian-mcp
```

This opens a browser UI listing the server's registered tools, where you can invoke them and inspect the request/response JSON.

## Access logging

Every MCP server in this repo logs its access (tool calls, resource reads, etc.) to `servers/<name>/.logs/` (hidden, like everything generated - see [Generated files](../README.md#generated-files)), regardless of which client connects (Inspector, Claude Desktop, or anything else) - the logging lives in the server process itself rather than in any one client. This directory is gitignored.

Each session (starting at `initialize`) gets its own log file, named `<count_up_id>_<date_time>_<client_name>.log` (`client_name` falls back to `unknown` if the client doesn't report one). The logging mechanism itself is shared, standalone middleware in [tools/logging](tools/logging) (`access-logging`) - a server just points it at its own log directory, e.g. in the Obsidian server's [server.py](obsidian/src/obsidian_mcp/server.py).

## Running tests

From the repo root, `uv run pytest` runs every package's test suite (both `servers/obsidian` and `servers/tools/logging`):

```bash
VAULT_PATH=/tmp/ci-vault uv run pytest
```

`VAULT_PATH` only matters for the Obsidian tests that need it - any writable path works for CI. Each package can still be tested on its own by running `uv run pytest` from within its own folder.

## Testing a single function directly

To quickly test one function without the Inspector, run it directly with Python. Example, for `list_notes` in the Obsidian server:

```bash
cd servers/obsidian
uv run python -c "from obsidian_mcp.server import list_notes; print(list_notes())"
```

## Obsidian submodule

Initialize Obsidian after cloning Nuru:

```bash
git submodule update --init servers/obsidian
```

Nuru tests the pinned Obsidian revision on every push and pull request. Obsidian
also runs its own tests on every push and pull request in its repository.
For a private Obsidian repository, set the Nuru Actions secret
`OBSIDIAN_REPO_TOKEN` to a fine-grained token with Contents read access to
`Omgitskillah/obsidian-4-llms`. Public repositories need no extra secret.

Commit and push Obsidian changes in its own repository first, then commit the
updated `servers/obsidian` submodule pointer in Nuru. The Obsidian repository
includes a copy of `access-logging` for its standalone CI workspace; Nuru's workspace
continues to use `tools/logging`. Nuru excludes the vendored logging tests
because it already tests the shared logging package.
