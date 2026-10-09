"""Run one prompt through a fresh Nuru Session and write what happened as JSON.

Usage: uv run python driver.py <out.json> <prompt>   (env: VAULT_PATH, OBSIDIAN_SEMANTIC_DB, NURU_DB_PATH, NURU_LOG_DIR)
"""
import json
import os
import sys
import time

out_path, prompt = sys.argv[1], sys.argv[2]

from core import session as S  # noqa: E402  (VAULT_PATH must be set before this import)
from core.providers.mcp_tools import MCPTools  # noqa: E402

S.OBSIDIAN_SERVER_PARAMS.env.update(
    VAULT_PATH=os.environ["VAULT_PATH"], OBSIDIAN_SEMANTIC_DB=os.environ["OBSIDIAN_SEMANTIC_DB"]
)
os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ["PATH"]

VAULT = os.environ["VAULT_PATH"]


def snapshot():
    """Every visible file in the vault -> its text (bytes length for non-text)."""
    files = {}
    for root, dirs, names in os.walk(VAULT):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in names:
            if n.startswith("."):
                continue
            path = os.path.join(root, n)
            try:
                files[os.path.relpath(path, VAULT)] = open(path, encoding="utf-8").read()
            except (UnicodeDecodeError, OSError):
                files[os.path.relpath(path, VAULT)] = f"<{os.path.getsize(path)} bytes>"
    return files


def vault_diff(before, after):
    diff = {"created": {}, "deleted": [], "changed": {}}
    for path, text in after.items():
        if path not in before:
            diff["created"][path] = text[-600:]
        elif before[path] != text:
            old = before[path]
            diff["changed"][path] = ("APPENDED: " + text[len(old):]) if text.startswith(old) else ("REWRITTEN: " + text[-600:])
    diff["deleted"] = sorted(set(before) - set(after))
    return diff


before = snapshot()
calls = []
_orig = MCPTools.call_tool_result


def recording(self, name, arguments):
    t = time.monotonic()
    r = _orig(self, name, arguments)
    calls.append({"tool": name, "args": arguments, "result": r.text[:1500], "is_error": r.is_error,
                  "s": round(time.monotonic() - t, 2)})
    return r


MCPTools.call_tool_result = recording

t0 = time.monotonic()
s = S.Session.start()
startup = time.monotonic() - t0
reply, thinking, events = [], [], []
t1 = time.monotonic()
try:
    for ev in s.ask(prompt):
        kind = type(ev).__name__
        if isinstance(ev, S.Text):
            reply.append(ev.text)
        elif isinstance(ev, S.Thinking):
            thinking.append(ev.text)
        elif isinstance(ev, S.ThinkingChunk):
            continue  # the whole arrives as Thinking
        elif isinstance(ev, S.ToolCall):
            events.append({"tool_call": ev.name})
        else:
            events.append({kind: getattr(ev, "message", getattr(ev, "reason", ""))})
finally:
    turn = time.monotonic() - t1
    s.close()

json.dump({"prompt": prompt, "reply": "".join(reply), "calls": calls, "events": events,
           "vault_diff": vault_diff(before, snapshot()),
           "thinking": thinking, "startup_s": round(startup, 1), "turn_s": round(turn, 1)},
          open(out_path, "w"), indent=2, ensure_ascii=False)
