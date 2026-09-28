# Nuru
Nuru to guide me into the AI future.  A hopeful darkness purge in a probably dystopia yonder. 

## Generated files

Everything Nuru and its servers write at runtime goes into a **hidden** (dot-prefixed) file or folder, and all of it is gitignored - it holds conversation history, vault content and API bookkeeping, and never leaves this machine. Every path can be moved with the environment variable next to it.

| Path | Written by | What it holds | Override |
|---|---|---|---|
| `nuru/.db/conversation.db` | Nuru (CLI and UI) | Every conversation and message | `NURU_DB_PATH` |
| `nuru/.db/decisions.sqlite3` (+ `-wal`, `-shm`) | laya-mcp and Nuru's harness | Laya's decisions with their state text, the tools the harness ran, labels, the classify cache | `database` in [`config/decision_layer.yaml`](config/decision_layer.yaml) |
| `nuru/.logs/` | Nuru | One JSONL log per session: turns, model calls, tool calls, indexing passes | `NURU_LOG_DIR` |
| `servers/<name>/.logs/` | each MCP server | One access log per client session (see [servers](servers/README.md)); the MCP Inspector's own logs too, when started as that README shows | `<NAME>_MCP_LOG_DIR` |
| `servers/deep-think/.file_cache.db` | deep-think | Which files were already uploaded to the expert, by content hash | `DEEP_THINK_MCP_DB_PATH` |
| `<vault>/.obsidian-mcp-index.sqlite3` | obsidian-mcp | Note id -> path for notes `create_note` made | - |
| `<vault>/.obsidian-mcp-semantic.sqlite3` | obsidian-mcp and Nuru's indexer | Semantic-search chunks and their vectors | `OBSIDIAN_SEMANTIC_DB` |

Not hidden, on purpose:

- **`<vault>/Templates/`** - written by `generate_templates`, but it is vault content: Obsidian's own "Insert template" has to see it.
- **Tooling caches** - `.venv/`, `__pycache__/`, `.pytest_cache/` (already hidden or conventional), and Laya's checkpoints in the Hugging Face cache (`~/.cache/huggingface/`, outside the repo; `uv run laya-mcp-fetch` fills it).

Before 2026-09-28 these were written without the dot (`logs/`, `db/`, `file_cache.db`). Those names stay gitignored, so a server still running the old code (a Claude Desktop session, say) can't leave untracked files behind; move their contents across once it has restarted.
