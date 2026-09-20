# obsidian-mcp

An MCP server exposing access to an Obsidian vault: listing, searching, reading, and writing notes, plus tag data.

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

## Dependencies

- **`mcp`** - The MCP server framework this package is built on (`MCPServer`, `@mcp.tool`, `@mcp.resource`); required to expose these tools/resources to a client at all.
- **`python-dotenv`** - Loads `VAULT_PATH` (and `OBSIDIAN_MCP_LOG_DIR`) from `.env` at startup, so the vault location isn't hardcoded or passed as a CLI flag.
- **`access-logging`** (workspace package, see [servers/tools/logging](../tools/logging)) - Provides `AccessLogMiddleware`, which logs every session's tool/resource access under `logs/` regardless of which client connects. Shared across servers in this repo rather than reimplemented here.
- **`python-ulid`** - Spec-correct ULID generation for note ids. Chosen over a hand-rolled implementation so the ID format (timestamp + randomness, base32 encoding) is backed by a maintained library instead of custom code.
- **`pyyaml`** - Serializes `create_note`'s frontmatter, rather than string-templating it, so key ordering, block-style lists, and quoting (e.g. a value starting with `{` being read back as a flow mapping) are handled correctly.
- **`unidecode`** - Transliterates a title to ASCII before slugifying it, so accented Latin letters and non-Latin scripts (CJK, etc.) survive into the filename as a readable approximation instead of being silently dropped.

## Note model

Notes created via `create_note` are **append-only**: their frontmatter is written exactly once, at creation, and is never rewritten. `id`, `created`, `path`, `tags`, and `aliases` are all server-owned - the caller cannot set them directly. This is why `write_note`/`replace_content` (whole-file overwrite) don't exist here: the only way to change an existing note is `append_note`, which can only add to the body.

An index (`.obsidian-mcp-index.sqlite3`, a dotfile inside the vault so it never shows up in `list_files`/`list_notes`) records `id -> path/type/title/author/created/supersedes` for every note `create_note` has made. It exists to resolve an id to a path (`append_note`) and to validate `supersedes` without scanning the vault - it only knows about notes made through `create_note`, not ones added directly in Obsidian. The file write and the index insert are treated as one operation: if the index insert fails, the just-written file is deleted rather than left as an orphan the index doesn't know about.

The index only learns about a filesystem change through this server's own tools - it has no way to notice the vault changing any other way (Obsidian sync, a git pull, a note deleted directly in the UI, the whole vault cleared out from under it). Left unreconciled, a stale row (its file gone, but its path still `UNIQUE`-constrained) can make a legitimate `create_note` collide with a path that no longer exists on disk. So every time the index is opened, it's first reconciled against the current filesystem: any row whose path no longer exists is dropped. There's no separate "startup" hook in this long-running process to do this once, so it runs on every open instead - simple, and correct regardless of when or how the vault changed. `rename_or_move_file` opens (and so reconciles) the index *before* physically moving the file, specifically so reconciliation can't see the old path already gone and delete the row out from under the repoint that's about to happen.

`rename_or_move_file` and `delete_file_or_directory` keep this index in sync: moving an indexed note repoints its row to the new path (rolling back the move if that repoint fails), and deleting one removes its row - either not done, `append_note` would report an id as unreachable ("indexed at X, but that file no longer exists") even though the note still exists, just moved, which is what archive-by-move relies on to work at all. Moving or deleting a file the index has no row for (an attachment, say) is a no-op on the index, not an error.

The template itself - what fields exist and their allowed values - lives in [`frontmatter_template.py`](src/obsidian_mcp/frontmatter_template.py), separate from `server.py`'s tool logic, and is a plain Python module (not an editable data file) on purpose: the whole point of versioning it is that nothing changes its shape without a code change and a version bump alongside it.

`create_note`'s eleven `type`s (`template.TYPE_FOLDER`) each map to a fixed folder:

| Folder | Types |
|---|---|
| `user/` | `directives`, `daily`, `correction` |
| `nuru/` | `profile`, `preferences`, `person`, `topic`, `index`, `transcript` |
| `shared/` | `project`, `session` |

`author` must be one of `user`, `nuru`, `both` (`template.VALID_AUTHORS`). Some types require exactly one extra field (`template.TYPE_FIELD`), passed as its own named parameter: `date` (`daily`), `targets` (`correction`), `session` (`transcript`, `session`) - e.g. `create_note(type="daily", ..., date="2026-09-14")`. These are explicit parameters, not `**kwargs`: a `**kwargs` catch-all is exposed in the MCP tool schema as a single required string field no caller can satisfy, which made `create_note` uncallable through any MCP client until this was fixed.

Every note's frontmatter carries a `version` field, stamped from `template.VERSION` at creation and never touched again. Because notes are append-only, this is the only way to know which frontmatter structure an existing note was written under once that structure changes - so `template.VERSION` gets bumped (semver) whenever `frontmatter_template.py`'s shape changes, or `server.py`'s `_build_frontmatter` output shape changes (a field added/renamed/reordered), but never bumped retroactively: an old note keeps the version number that was current when it was actually written. A future tool that reads notes back can branch on `version` to handle older structures correctly instead of assuming today's shape everywhere.

`frontmatter_template.py` carries the authoritative definition of what each version number means - a `version` isn't just an incrementing counter, it's a key into that table. **`0.1.0`** (current) means exactly: field order `id, version, type, created, author, supersedes, [<type-specific field>], tags, aliases`; `id` a ULID minted once and never touched again; `created` `"YYYY-MM-DDTHH:mm"` server clock; `author` one of `user`/`nuru`/`both`; `supersedes` an existing note's id or blank; the type-specific field (present only for types in `TYPE_FIELD`) validated per its own rules (`date`: `"YYYY-MM-DD"`; `session`: `"YYYY-MM-DD-NN"`; `targets`: a list of existing note ids); `tags` a block list of `[type, ...extra]` with no tag containing whitespace; `aliases` a block list of `[title, id]`.

**Not yet implemented** (deferred from the original spec, to keep each change independently reviewable): `read_note`/`search_notes` returning ids instead of paths, and archive-by-move (replacing a `status: archived` field, which append-only rules out).

### Template files (`Templates/`)

`generate_templates()` writes the eleven types' skeleton files to `Templates/` at the vault root - one per type, named `01 Profile.md` through `11 Correction.md` (see [`template_bodies.py`](src/obsidian_mcp/template_bodies.py) for the exact filenames and body text). These are for Obsidian's own "Insert template" feature, used directly by a human (or by Nuru filling one in), not by `create_note` - which builds a note's frontmatter itself and never reads these files.

A template file's frontmatter has the same shape and field order as a real note's, but with placeholder or blank values: `created` uses Obsidian's own `{{date}}`/`{{time}}` substitution tokens (quoted, since `{{...}}` unquoted parses as a YAML flow mapping); `id` is left blank - there's no `{{id}}` token, so a human or the MCP fills it in when the note is actually created from the template, the same way `id` is blank until `create_note` mints one. `aliases` is a single-item list (just `{{title}}`) for the same reason - there's no id yet to be the second entry, and a dangling blank list item parses as a `null` alias in Obsidian rather than simply not existing. `author` gets a concrete per-type default (`template.TEMPLATE_AUTHOR`), not a blank, since unlike `id` it has no natural "fill me in" placeholder story and a real note requires it. A type's extra frontmatter field (`date`, `targets`, `session`) is similarly `{{date}}` where that token applies, or left blank/empty (`targets: []`) where no token exists for it (e.g. `session`'s `<YYYY-MM-DD>-NN` has no token for `NN`).

By default `generate_templates()` does not overwrite a template file that already exists - a file already reflecting the current spec, or one a human has since hand-edited, isn't silently clobbered. Pass `overwrite=True` to force every template back to the generated form.

## Security

All path-taking tools (`list_files_in_directory`, `read_note`, `append_note`, `rename_or_move_file`, `delete_file_or_directory`, `generate_templates`) are confined to `VAULT_PATH`: `..`/absolute-path traversal is rejected, and a symlink stored inside the vault that points outside it is also rejected (checked against its resolved, real location, not just its name). `create_note`'s path is server-derived (`type` folder + slugified `title`), never caller-supplied, so it goes through the same containment check but can't be steered outside the vault by input.

`rename_or_move_file` and `delete_file_or_directory` additionally refuse to touch anything inside a dotfile/dot-directory (`.obsidian/`, this server's own `.obsidian-mcp-index.sqlite3`) - the listing tools already excluded these from what they *show*, but that was cosmetic: nothing previously stopped a structural tool from reaching in and deleting `.obsidian/app.json` or `plugins/`. This closes that specifically; it does not filter a hidden file out of a directory being deleted as a whole (deleting a folder that happens to contain a dotfile still removes it).

### Permissions

The two structural/destructive tools (`rename_or_move_file`, `delete_file_or_directory`) also refuse to touch anything under `user/` (`directives`, `daily`, `correction`) - only `create_note` (gated by `type`) and `append_note` can write there, so nothing else can rename or delete a boundary-setting note out from under it.

This is a narrow slice of the permission model the original design called for, not the whole thing: there's no notion here of *who* is calling (a human's explicit instruction vs. an autonomous decision), so it can't enforce "Nuru read-only" in the fuller sense that implies - it only enforces "no tool but these two, regardless of caller, can touch `user/`". `create_note`/`append_note` remain free to write there, since blocking them would break every `user/`-folder note type's ability to be created or grown at all.

## Reliability

Every filesystem-mutating tool (`create_note`, `append_note`, `rename_or_move_file`, `delete_file_or_directory`, `generate_templates`) runs its actual disk I/O through a small thread pool with a 10s deadline. If the underlying filesystem call stalls (an unavailable network mount, a stuck disk), the tool raises `TimeoutError` immediately instead of leaving the caller hanging - Python has no safe way to forcibly kill a thread stuck in a blocking syscall, so the abandoned worker thread is left to finish (or never does) rather than cancelled.

Because of that, `create_note` uses an atomic exclusive-create (`open(path, "x")`) rather than a separate exists-check-then-write: a client retrying an identical `create_note` call after a timeout is safe even if the original, abandoned write is still landing - the two can't silently race and clobber each other, and the retry still correctly fails with `FileExistsError` if a note is already there.

## Error handling

Every tool is wrapped (`@_tool` in `server.py`) so its own anticipated failures - bad input, not found, already exists, permission denied, a timed-out write (`ValueError`, `FileNotFoundError`, `FileExistsError`, `PermissionError`, `TimeoutError`) - reach the caller as `ToolError` with the original exception's type and message intact. Without this, the MCP framework only forwards a raised exception's message to the client for `ToolError` (or a subclass); anything else is treated as a crash and replaced with a bare `Error executing tool <name>`, discarding the message entirely - every failure this server raises looked identical from the outside. A genuinely unexpected exception (a real bug, not one of the five types above) still gets that generic treatment, which is the correct behavior for something truly unanticipated.

`read_note` raises `FileNotFoundError` for a missing note rather than returning an empty string, which was indistinguishable from a note that's genuinely empty.

## Tools

- **`list_notes()`** - List all notes (Markdown files) in the vault, as paths relative to the vault root. Excludes `Templates/` (placeholder skeletons, not notes) - as do `search_notes`, `list_tags`, and `count_tags`.
- **`list_files()`** - List all files in the vault (notes and attachments alike), as paths relative to the vault root. Excludes Obsidian's internal `.obsidian/` config folder and dotfiles.
- **`list_files_in_directory(directory)`** - List all files recursively within a subdirectory of the vault. If `directory` isn't found directly under the vault root, the vault is searched recursively for a directory with that name, and the first match (alphabetically, by path) is used. Returns `{directory, count, files}`; when `files` is empty (no matching directory, or an empty one) a `hint` explains why.
- **`search_notes(query)`** - Search notes for a literal substring, case-insensitive. Returns `{query, count, matches}` with `matches` as note paths; when nothing matches, a `hint` says how to retry (one distinctive word, or a different format). Returns a dict rather than a bare list so an empty result still reaches the model as a readable message instead of a blank tool response.
- **`read_note(name)`** - Return the full contents of a note by name, with or without the `.md` extension. If not found directly under the vault root, the vault is searched recursively for a note with that name, and the first match (alphabetically, by path) is used. Raises `FileNotFoundError` if no matching note is found.
- **`create_note(type, title, author, body="", tags=None, supersedes=None, date=None, targets=None, session=None)`** - Create a new note with server-owned frontmatter (see Note model above). `date`/`targets`/`session` are only valid (and are each required) for the type they belong to - see Note model - and are validated, not just accepted: `date`/`session` must be well-formed with a real calendar date, and every id in `targets` must already exist in the index, the same as `supersedes`. `title` is transliterated to ASCII for the filename (`unidecode`) and falls back to the note's own id if nothing survives (an all-CJK or all-emoji title, say) - only a title with no real content at all (empty or all whitespace) is rejected. No tag in `tags` may contain whitespace. A `type` under `user/` (`directives`, `daily`, `correction`) requires `author="user"`. Raises `ValueError` for any of the above, or `FileExistsError` if the derived path already exists. Returns `{"id": <ulid>, "path": <vault-relative path>}`.
- **`append_note(id, content)`** - Append `content` to the body of the note with this `id` (looked up via the index, never a path - a typo'd id fails loudly with `FileNotFoundError` instead of silently creating a new note). Inserts a blank line before `content` if the note doesn't already end with one, so the appended text starts its own block instead of running onto the end of the note's last line - `content` doesn't need a leading newline.
- **`generate_templates(overwrite=False)`** - Write the eleven Obsidian template files to `Templates/` at the vault root (see Template files above). By default, a file that already exists is left alone; pass `overwrite=True` to force regeneration. Returns `{"written": [...], "skipped": [...]}` (vault-relative paths).
- **`rename_or_move_file(source_path, destination_path)`** - Rename or move a file from `source_path` to `destination_path` (both relative to the vault root), repointing its index row if it's an indexed note (see Note model above). Raises `FileNotFoundError` if the source doesn't exist, `FileExistsError` if the destination already does, or `PermissionError` if either path is hidden (e.g. `.obsidian/`) or under `user/` (see Permissions above).
- **`delete_file_or_directory(path)`** - Delete the file or directory at `path` (directories are removed recursively), removing the index row for any indexed note among what's deleted (see Note model above). Raises `FileNotFoundError` if nothing exists there, `ValueError` if `path` resolves to the vault root itself, or `PermissionError` if `path` is hidden (e.g. `.obsidian/`) or under `user/` (see Permissions above). Returns the vault-relative paths of everything actually deleted - the path itself, plus every file and subdirectory under it if it was a directory - not just the one path passed in.

Not implemented: patching content near a heading, block reference, or frontmatter field (an atomic/surgical insert) was judged too complex to safely review and ship alongside the tools above.

## Resources

- **`vault://tags`** (`tags`) - All unique tags (e.g. `project` for `#project`) used across notes in the vault.
- **`vault://tags/counts`** (`tag_counts`) - Occurrence counts for each unique tag across notes in the vault.

## Testing

```bash
uv run pytest
```

See the [servers README](../README.md) for running the whole repo's tests together from the root.
