# Tool checks (2026-10-02/03)

The harness behind the `*-check-2026-10-0*.md` notes in `nuru/evals/`: one Nuru turn per prompt, in a
fresh process, against a reset scratch vault, recording every tool call, its result and what changed on disk.

- `driver.py` - one prompt through `Session.start()` / `ask()`; writes `out/<id>.json` (calls, reply,
  thinking, timings, vault diff).
- `runner.py` - runs prompt ids from `$PROMPTS` (default `prompts.json`): resets `vault/` from
  `vault-pristine/` and `semantic.sqlite3` from `semantic-pristine.sqlite3`, gives each run its own
  decision config (`cfg/<id>.yaml`) logging to `dec/<id>.sqlite3` - never Nuru's real decision log.
- `summarise.py` - prints each result.

Set up next to these files before a run (all gitignored-worthy scratch, not committed):
1. `vault-pristine/`: a copy of dummyVault plus `archive/nuru/old-router-notes.md` and
   `trash/nuru/pancake-recipe.md` (the seeded archived and trashed notes; see tool-usage-check).
2. `semantic-pristine.sqlite3`: `VAULT_PATH=<this dir>/vault OBSIDIAN_SEMANTIC_DB=<this dir>/semantic-pristine.sqlite3 uv run obsidian-mcp-index` in servers/obsidian, after copying vault-pristine to vault/.
3. `PROMPTS=prompts_corrections.json caffeinate -i python3 runner.py 901 902 ...`

`prompts_corrections.json` (901-907) has not been run yet.
