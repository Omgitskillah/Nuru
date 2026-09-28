"""Keeps the obsidian server's semantic index current while Nuru runs.

A daemon thread looks over the vault every INTERVAL_SECONDS and embeds the notes that
changed since it last looked (all of them, the first time) - the obsidian server's
semantic_search tool only reads that index. It runs here, in Nuru's own process, so the
window can show when it's busy by reading `pending` instead of asking the server.

Settings come from the obsidian server's own .env (VAULT_PATH, OBSIDIAN_EMBED_MODEL, ...),
so Nuru and the server always agree on which index and which model; a variable already
set in Nuru's environment wins, as it would for the server.
"""

import contextlib
import os
import sys
import threading
from pathlib import Path

from dotenv import dotenv_values
from obsidian_mcp import semantic

OBSIDIAN_ENV = Path(__file__).resolve().parents[3] / "servers" / "obsidian" / ".env"
INTERVAL_SECONDS = 30.0
STOP_WAIT_SECONDS = 5.0


class VaultIndexer:
    def __init__(self, config: semantic.Config, embed: semantic.Embed, interval: float = INTERVAL_SECONDS) -> None:
        self.config = config
        self.embed = embed
        self.interval = interval
        self.pending = 0  # notes left to embed in the pass under way; 0 while idle. Read from the GUI thread.
        self.error: str | None = None  # why the last pass failed, if it did
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="nuru-vault-indexer", daemon=True)

    @classmethod
    def from_env(cls) -> "VaultIndexer | None":
        """An indexer set up like the obsidian server, or None if no vault is configured."""
        env = {**{k: v for k, v in dotenv_values(OBSIDIAN_ENV).items() if v is not None}, **os.environ}
        if not env.get("VAULT_PATH"):
            return None
        config = semantic.load_config(Path(env["VAULT_PATH"]).expanduser().resolve(), env)
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
        """One pass: embed whatever changed, keeping `pending` up to date while it does."""
        try:
            with contextlib.closing(semantic.connect(self.config)) as conn:
                return semantic.refresh(
                    conn, self.config, self.embed, on_progress=self._set_pending, should_stop=self._stop.is_set
                )
        finally:
            self.pending = 0

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
