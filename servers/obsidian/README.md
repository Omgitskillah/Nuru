# obsidian-mcp

An MCP server exposing read-only access to an Obsidian vault: listing, searching, and reading notes, plus tag data.

## Setup

Copy `.env.example` to `.env` and set `VAULT_PATH` to your vault's absolute path:

```bash
cp .env.example .env
# then edit .env
```

```
VAULT_PATH=/absolute/path/to/vault
```

Install dependencies and run:

```bash
uv sync
uv run obsidian-mcp
```

This starts the server over stdio and waits for a host (e.g. Claude Desktop) to connect - see the [servers README](../README.md) for running it, testing it with the MCP Inspector, and access logging.

## Security

All path-taking tools (`list_files_in_directory`, `read_note`) are confined to `VAULT_PATH`: `..`/absolute-path traversal is rejected, and a symlink stored inside the vault that points outside it is also rejected (checked against its resolved, real location, not just its name).

## Tools

- **`list_notes()`** - List all notes (Markdown files) in the vault, as paths relative to the vault root.
- **`list_files()`** - List all files in the vault (notes and attachments alike), as paths relative to the vault root. Excludes Obsidian's internal `.obsidian/` config folder and dotfiles.
- **`list_files_in_directory(directory)`** - List all files recursively within a subdirectory of the vault. If `directory` isn't found directly under the vault root, the vault is searched recursively for a directory with that name, and the first match (alphabetically, by path) is used. Returns an empty list if no matching directory is found.
- **`search_notes(query)`** - Search notes for a substring, case-insensitive. Returns paths of matching notes, or an empty list if none match.
- **`read_note(name)`** - Return the full contents of a note by name, with or without the `.md` extension. If not found directly under the vault root, the vault is searched recursively for a note with that name, and the first match (alphabetically, by path) is used. Returns an empty string if no matching note is found.

## Resources

- **`vault://tags`** (`tags`) - All unique tags (e.g. `project` for `#project`) used across notes in the vault.
- **`vault://tags/counts`** (`tag_counts`) - Occurrence counts for each unique tag across notes in the vault.

## Testing

```bash
uv run pytest
```

See the [servers README](../README.md) for running the whole repo's tests together from the root.
