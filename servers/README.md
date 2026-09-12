# Servers

Each MCP server lives in its own self-contained folder here (own `pyproject.toml`, deps, tests).

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
npx @modelcontextprotocol/inspector uv run obsidian-mcp
```

This opens a browser UI listing the server's registered tools, where you can invoke them and inspect the request/response JSON.

## Testing a single function directly

To quickly test one function without the Inspector, run it directly with Python. Example, for `list_notes` in the Obsidian server:

```bash
cd servers/obsidian
uv run python -c "from obsidian_mcp.server import list_notes; print(list_notes())"
```
