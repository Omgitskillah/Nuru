# Nuru
Nuru to guide me into the AI future.  A hopeful darkness purge in a probably dystopia yonder. 

## Settings

Every subsystem's settings live in the repo root, one file each, and every one is gitignored. The `.example` beside each is the tracked template: copy it and fill it in.

| File | Read by | Holds |
|---|---|---|
| `.env.nuru` | Nuru (CLI, UI, evals) | the models (`NURU_BASE_MODEL`, `MODEL`), `THINK`, context size |
| `.env.obsidian` | the obsidian server, and Nuru's indexer and laya server (for the same vault) | `VAULT_PATH`, the embedding model |
| `.env.deep-think` | deep-think (`ask_an_expert`), and Nuru's expert tier | `CLAUDE_API_KEY`, `DEEP_THINK_BASE_DIR` |
| `.env.laya` | the laya server when started on its own (optional) | `VAULT_PATH` for `classify_notes` |

A variable already set in the environment always wins over the file. Nuru hands its servers only what they need, since MCP servers start with a stripped-down environment.

## Generated files

Everything Nuru and its servers write at runtime goes into a **hidden** (dot-prefixed) file or folder, and all of it is gitignored - it holds conversation history, vault content and API bookkeeping, and never leaves this machine. Every path can be moved with the environment variable next to it.

| Path | Written by | What it holds | Override |
|---|---|---|---|
| `nuru/.db/conversation.db` | Nuru (CLI and UI) | Every conversation and message | `NURU_DB_PATH` |
| `nuru/.db/decisions.sqlite3` (+ `-wal`, `-shm`) | laya-mcp and Nuru's harness | Laya's decisions with their state text, the tools the harness ran, labels, the classify cache | `database` in [`config/decision_layer.yaml`](config/decision_layer.yaml) |
| `nuru/.logs/` | Nuru | One JSONL log per session: turns, model calls, tool calls, indexing passes | `NURU_LOG_DIR` |
| `servers/<name>/.logs/` | each MCP server | One access log per client session (see [servers](servers/README.md)); the MCP Inspector's own logs too, when started as that README shows | `<NAME>_MCP_LOG_DIR` |
| `servers/deep-think/.file_cache.db` | deep-think | Which files were already uploaded to the expert, by content hash | `DEEP_THINK_MCP_DB_PATH` |
| `nuru/evals/**/.runs/` | the eval scripts | Raw eval results and scratch vault copies (see [decision-layer evals](nuru/evals/decision_layer/README.md)) | - |
| `<vault>/.obsidian-mcp-index.sqlite3` | obsidian-mcp | Note id -> path for notes `create_note` made | - |
| `<vault>/.obsidian-mcp-semantic.sqlite3` | obsidian-mcp and Nuru's indexer | Semantic-search chunks and their vectors | `OBSIDIAN_SEMANTIC_DB` |

Not hidden, on purpose:

- **`<vault>/Templates/`** - written by `generate_templates`, but it is vault content: Obsidian's own "Insert template" has to see it.
- **Tooling caches** - `.venv/`, `__pycache__/`, `.pytest_cache/` (already hidden or conventional), and Laya's checkpoints in the Hugging Face cache (`~/.cache/huggingface/`, outside the repo; `uv run laya-mcp-fetch` fills it).

Before 2026-09-28 these were written without the dot (`logs/`, `db/`, `file_cache.db`). Those names stay gitignored, so a server still running the old code (a Claude Desktop session, say) can't leave untracked files behind; move their contents across once it has restarted.
