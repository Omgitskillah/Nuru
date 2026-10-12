"""Keeps the obsidian server's semantic index current while Nuru runs.

A daemon thread looks over the vault every INTERVAL_SECONDS and embeds the notes that
changed since it last looked (all of them, the first time) - the obsidian server's
semantic_search tool only reads that index. It runs here, in Nuru's own process, so the
window can show when it's busy by reading `pending` instead of asking the server.

Settings come from the obsidian server's .env.obsidian in the repo root (VAULT_PATH, OBSIDIAN_EMBED_MODEL, ...),
so Nuru and the server always agree on which index and which model; a variable already
set in Nuru's environment wins, as it would for the server.
"""

import contextlib
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from dotenv import dotenv_values
from obsidian_mcp import semantic

OBSIDIAN_ENV = Path(__file__).resolve().parents[3] / ".env.obsidian"
INTERVAL_SECONDS = 30.0
STOP_WAIT_SECONDS = 5.0
# A full first build names every note; the terminal line lists this many and counts the rest.
_PATHS_SHOWN = 5


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def describe(result: semantic.RefreshResult) -> str:
    """What a pass changed, in a few words: "indexed 1 note", "indexed 2 notes, moved 1, removed 1"."""
    parts = []
    if result.embedded:
        parts.append(f"indexed {_plural(len(result.embedded), 'note')}")
    if result.moved:
        parts.append(f"moved {len(result.moved)}")
    if result.removed:
        parts.append(f"removed {len(result.removed)}")
    return ", ".join(parts)


def _paths_line(result: semantic.RefreshResult) -> str:
    """The notes a pass touched, for the terminal: embedded, moved (from -> to) and removed paths."""
    paths = [*result.embedded, *(f"{a} -> {b}" for a, b in result.moved), *(f"{p} (removed)" for p in result.removed)]
    shown = ", ".join(paths[:_PATHS_SHOWN])
    return shown + (f" and {len(paths) - _PATHS_SHOWN} more" if len(paths) > _PATHS_SHOWN else "")


def semantic_db(vault: Path) -> str:
    """The semantic index for `vault`: OBSIDIAN_SEMANTIC_DB from this process's environment if set;
    .env.obsidian's only when `vault` is .env.obsidian's own vault; otherwise the notes server's default,
    inside `vault`. Never another vault's index: a refresh would drop that vault's notes from it, and
    search would answer from them."""
    if os.environ.get("OBSIDIAN_SEMANTIC_DB"):
        return os.environ["OBSIDIAN_SEMANTIC_DB"]
    configured = dotenv_values(OBSIDIAN_ENV)
    if configured.get("OBSIDIAN_SEMANTIC_DB") and configured.get("VAULT_PATH") \
            and Path(configured["VAULT_PATH"]).expanduser().resolve() == Path(vault).resolve():
        return configured["OBSIDIAN_SEMANTIC_DB"]
    return str(Path(vault).resolve() / semantic.DB_NAME)


class VaultIndexer:
    def __init__(self, config: semantic.Config, embed: semantic.Embed, interval: float = INTERVAL_SECONDS) -> None:
        self.config = config
        self.embed = embed
        self.interval = interval
        self.pending = 0  # notes left to embed in the pass under way; 0 while idle. Read from the GUI thread.
        self.error: str | None = None  # why the last pass failed, if it did
        # A pass that embeds one note is over in ~0.1 s, too quick to see; these let the window say
        # so afterwards. `changes` counts the passes that changed the index, `last_change` says what
        # the latest one did. last_change is set first, so a reader that sees a new count sees its text.
        self.changes = 0
        self.last_change = ""
        self.on_change: Callable[[semantic.RefreshResult], None] | None = None  # e.g. the session log
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="nuru-vault-indexer", daemon=True)

    @classmethod
    def from_env(cls) -> "VaultIndexer | None":
        """An indexer set up like the obsidian server, or None if no vault is configured."""
        env = {**{k: v for k, v in dotenv_values(OBSIDIAN_ENV).items() if v is not None}, **os.environ}
        if not env.get("VAULT_PATH"):
            return None
        vault = Path(env["VAULT_PATH"]).expanduser().resolve()
        config = semantic.load_config(vault, {**env, "OBSIDIAN_SEMANTIC_DB": semantic_db(vault)})
        return cls(config, semantic.ollama_embedder(config))

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Stop after the note being embedded, if any. Doesn't wait long: the thread is a daemon,
        and SQLite rolls back a note it didn't finish."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(STOP_WAIT_SECONDS)

    def run_once(self) -> semantic.RefreshResult:
        """One pass: embed whatever changed, keeping `pending` up to date while it does. A pass
        that changed the index is announced: a line on stderr naming the notes, `changes` and
        `last_change` for the window, and `on_change` for the session log."""
        try:
            with contextlib.closing(semantic.connect(self.config)) as conn:
                conn.execute("PRAGMA busy_timeout=1000")  # background work yields promptly; interactive connections keep 30 s
                result = semantic.refresh(
                    conn, self.config, self.embed, on_progress=self._set_pending,
                    should_stop=self._stop.is_set
                )
        finally:
            self.pending = 0
        if summary := describe(result):
            print(f"Nuru: semantic index - {summary}: {_paths_line(result)}", file=sys.stderr)
            self.last_change = summary
            self.changes += 1
            if self.on_change is not None:
                self.on_change(result)
        return result

    def _set_pending(self, left: int) -> None:
        self.pending = left

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
                self.error = None
            except Exception as e:  # Ollama down, a model mismatch: say so once, and try again next pass
                message = f"{type(e).__name__}: {e}"
                if message != self.error:
                    print(f"Nuru: couldn't update the semantic index. {message}", file=sys.stderr)
                self.error = message
            self._stop.wait(self.interval)
