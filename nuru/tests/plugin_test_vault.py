"""NuruPluginTestVault: the disposable vault WP01's acceptance runs use, built from the definitions here,
never from a personal vault. It has nuru/, shared/ and user/ notes, Unicode names, duplicate basenames,
a note with hostile HTML, a large note and a note to edit behind Nuru's back (a stale revision).

    uv run --locked python nuru/tests/plugin_test_vault.py <dir>

makes <dir>/NuruPluginTestVault and prints the SHA-256 of every authored file as JSON. Tests use
build(), snapshot() and changes() to assert the exact set of files a run changed.
"""

import hashlib
import json
import sys
from pathlib import Path

NAME = "NuruPluginTestVault"

HOSTILE_NOTE = (
    "---\nauthor: user\n---\n# Hostile\n\n<script>alert('x')</script>\n<img src=x onerror=\"alert(1)\">\n"
    "<iframe src=\"https://example.com\"></iframe>\n[click](javascript:alert(1)) "
    "![pixel](http://127.0.0.1:27199/pixel.png)\n\n<b onclick=\"alert(2)\">bold</b>\n"
)


def _large_note() -> str:
    """~300 KB of numbered sections, the same bytes every time."""
    sections = [f"## Section {i}\n\n" + f"Entry {i}: the sensor log reads {i * 7 % 101} units. " * 40 + "\n"
                for i in range(150)]
    return "# Sensor log\n\n" + "\n".join(sections)


NOTES = {
    # The scripted server's scenarios name these three.
    "nuru/Trip.md": "# Trip\n\nRome in May.\n",
    "user/Budget.md": "# Budget\n\n1200 EUR.\n",
    "shared/Team.md": "# Team\n\nShared with the team.\n",
    # Duplicate basenames in three folders.
    "nuru/Plan.md": "# Plan (Nuru)\n\n- [ ] book the train\n",
    "shared/Plan.md": "# Plan (shared)\n\nThe team's plan.\n",
    "user/Plan.md": "---\nauthor: user\n---\n# Plan (mine)\n\nOnly I edit this.\n",
    # Unicode names and text.
    "nuru/Café ☕ notes.md": "# Café ☕ notes\n\nCrème brûlée, naïve façade, 東京, Ελλάδα, emoji 🏛️.\n",
    "shared/日本語のメモ.md": "# 日本語のメモ\n\nローマ旅行は五月です。\n",
    "user/Ünïcödé — dash.md": "---\nauthor: user\n---\n# Ünïcödé — dash\n\nZero-width joiner: 👩‍💻.\n",
    "user/Hostile.md": HOSTILE_NOTE,
    "nuru/Sensor log.md": _large_note(),
    # Edited outside Nuru between a read and a write.
    "nuru/Stale.md": "# Stale\n\nRevision 1.\n",
    "archive/nuru/Old.md": "# Old\n\nArchived.\n",
}


def build(parent: Path) -> Path:
    """Make a fresh <parent>/NuruPluginTestVault and return its path. Refuses one that already exists."""
    root = Path(parent) / NAME
    root.mkdir(parents=True)
    for name, text in NOTES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(text.encode())
    (root / "trash").mkdir()
    return root


def snapshot(root: Path) -> dict[str, str]:
    """{vault-relative path: SHA-256} of every file, Obsidian's own folder left out."""
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(root).rglob("*"))
            if path.is_file() and not path.relative_to(root).parts[0].startswith(".")}


def changes(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    """What changed between two snapshots: added, changed and removed paths, sorted."""
    return {"added": sorted(after.keys() - before.keys()),
            "changed": sorted(p for p in before.keys() & after.keys() if before[p] != after[p]),
            "removed": sorted(before.keys() - after.keys())}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <dir>")
    vault = build(Path(sys.argv[1]))
    print(json.dumps({"vault": str(vault), "files": snapshot(vault)}, ensure_ascii=False, indent=1))
