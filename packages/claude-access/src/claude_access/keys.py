"""The Anthropic API key, read in one place for everything that calls Claude.

ANTHROPIC_API_KEY if the process has one, else CLAUDE_API_KEY from the environment, else CLAUDE_API_KEY
from the repo root's gitignored .env.deep-think. The key is never copied into code, config or logs. A
packaged Nuru replaces this with its own settings or the system keychain.
"""

import os
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[4]


def api_key() -> str | None:
    return (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
            or dotenv_values(REPO_ROOT / ".env.deep-think").get("CLAUDE_API_KEY") or None)
