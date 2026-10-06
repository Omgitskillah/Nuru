"""Sequence fixtures augment the original numbered single-prompt suite."""
import json
import re
from pathlib import Path


def load_cases(path: Path) -> dict[int, dict]:
    cases = json.loads(path.read_text())
    for key, case in cases.items():
        int(key)
        if not isinstance(case, dict) or not isinstance(case.get("turns"), list) or not case["turns"]:
            raise ValueError(f"case {key}: expected a nonempty turns list")
        if "plan" in case and (not isinstance(case["plan"], dict) or not isinstance(case["plan"].get("steps"), list)):
            raise ValueError(f"case {key}: plan must have steps")
        for turn in case["turns"]:
            if not isinstance(turn.get("prompt"), str) or not turn["prompt"].strip():
                raise ValueError(f"case {key}: expected a nonempty prompt")
            if not isinstance(turn.get("checks"), dict) or not turn["checks"]:
                raise ValueError(f"case {key}: expected per-turn regex checks")
            for pattern in turn["checks"].values():
                re.compile(pattern)
    return {int(key): case for key, case in cases.items()}


def vault_snapshot(root: Path) -> dict[str, bytes]:
    ignored = {".obsidian", ".obsidian-mcp-index.sqlite3", ".obsidian-mcp-semantic.sqlite3",
               ".obsidian-mcp-write.lock"}
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and not any(part in ignored for part in p.relative_to(root).parts)}


def snapshot_changes(before: dict[str, bytes], after: dict[str, bytes]) -> list[dict]:
    return [{"path": p, "before": before[p].decode(errors="replace") if p in before else None,
             "after": after[p].decode(errors="replace") if p in after else None}
            for p in sorted(before.keys() | after.keys()) if before.get(p) != after.get(p)]
