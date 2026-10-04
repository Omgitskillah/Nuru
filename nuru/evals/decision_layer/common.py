"""What the decision-layer evals share: paths, the dummyVault notes the write tests touch, the
argument-step prompts, and a single-call Ollama client that records what a call cost.

Every eval writes its raw results under `.runs/` next to this file (hidden and gitignored, like
everything generated). Nothing here touches the real vault: runs that write use scratch copies of
dummyVault, and the single-call evals never execute a tool at all.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RUNS = HERE / ".runs"
DUMMY_VAULT = Path(os.environ.get("NURU_EVAL_VAULT", "/Users/aluchoayisi/Desktop/dummyVault"))
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# The notes write tests 28-31 are about (dummyVault ids).
PRIYA = "01M2X9M15NDE1FJYDMC0SPGRVA"  # nuru/priya-raman.md
VENDOR_EMAIL = "01M2X9PEPF0SAHPTJRFF6Z4KQJ"  # nuru/suspicious-vendor-email-paste.md
AREA_MONITOR = "01M2NT6HP8THJKVENCBHBQBN5W"  # shared/Projects/area-monitor-device-redesign.md

# Argument-only steps, as they happen mid-turn: the user's request, the search the model ran (its
# real result on dummyVault, as a tool message), then the harness asking for one tool's arguments.
# The note IDs the model may use are the ones that search returned. `check` says whether the
# arguments are right, not just well-formed.
ARGUMENT_STEPS = [
    # Since the 2026-10 tools purge every write is write_note. The old "impossible append" step (a
    # line for a person with no note) is gone: write_note now creates a missing note, so there is no
    # longer one right answer to it.
    {
        "name": "append_priya", "tool": "write_note", "query": "Priya",
        "request": "Add a line to Priya's note saying she prefers async messages on Fridays.",
        "check": lambda a: ("priya" in str(a.get("note", "")).lower() or a.get("note") == PRIYA)
        and a.get("mode", "append") == "append"
        and "async" in str(a.get("content", "")).lower() and "friday" in str(a.get("content", "")).lower(),
    },
    {
        "name": "correction_sara", "tool": "write_note", "query": "SARA-R510S",
        "request": "File a correction: the SARA-R510S lead time is 20 weeks, not 26.",
        "check": lambda a: a.get("type") == "correction" and "20" in str(a.get("content", ""))
        and (bool(set(a.get("targets") or []) & {VENDOR_EMAIL, AREA_MONITOR}) or "[[" in str(a.get("content", ""))),
    },
    {
        "name": "transcript_standup", "tool": "write_note", "query": None,
        "request": "Create a transcript of today's standup: we discussed the soak test and the supercap.",
        "check": lambda a: a.get("type") == "transcript" and "soak" in str(a.get("content", "")).lower()
        and "supercap" in str(a.get("content", "")).lower(),
    },
    {
        "name": "daily_gym", "tool": "write_note", "query": None,
        "request": "Create a daily note for today saying the soak test is running and I'm going to the gym.",
        "check": lambda a: a.get("type") == "daily" and "soak" in str(a.get("content", "")).lower()
        and "gym" in str(a.get("content", "")).lower(),
    },
]


def tool_schemas() -> dict[str, dict]:
    """The obsidian server's live input schemas (read without starting it as a server)."""
    import asyncio

    os.environ.setdefault("VAULT_PATH", str(DUMMY_VAULT))
    from obsidian_mcp import server

    return {t.name: {"input_schema": t.input_schema, "description": t.description}
            for t in asyncio.run(server.mcp.list_tools())}


def search(query: str) -> dict:
    """The obsidian server's own search, run read-only on dummyVault."""
    os.environ.setdefault("VAULT_PATH", str(DUMMY_VAULT))
    from obsidian_mcp import server

    return server.search(query)


def argument_messages(step: dict, description: str) -> tuple[list[dict], list[str]]:
    """(the conversation up to the argument step, the note IDs its search returned)."""
    now = datetime.now().astimezone()
    messages = [{"role": "user", "content": f"[Current date and time: {now:%A %Y-%m-%d %H:%M %Z}]\n\n{step['request']}"}]
    known_ids: list[str] = []
    if step["query"]:
        found = search(step["query"])
        known_ids = [m["id"] for m in found["matches"] if m.get("id")]
        messages += [
            {"role": "assistant", "tool_calls": [{"function": {"name": "search", "arguments": {"query": step["query"]}}}]},
            {"role": "tool", "content": json.dumps(found)},
        ]
    messages.append({"role": "user", "content": (
        f"[Nuru] Next step: `{step['tool']}` ({description.split('.')[0].lower()}). Give its arguments as JSON, "
        'using only note IDs from the results above - or {"cannot_do": "<why>"} if it can\'t be done.'
    )})
    return messages, known_ids


def chat_once(model: str, messages: list[dict], think, *, placement: str = "top", fmt: dict | None = None,
              options: dict | None = None) -> dict:
    """One non-streamed /api/chat call and what it cost. `placement` puts `think` at the top level
    (Ollama's documented field) or inside `options` (as Ollama's Granite page shows).

    Sends only what Nuru itself sends - `num_ctx` as Session caps it - so everything else (sampling,
    `num_predict`) comes from the model's Modelfile, as it does in a real turn. `options` adds to that
    only where an eval means to (think_modes' temperature 0)."""
    from core import session

    payload = {"model": model, "messages": messages, "stream": False,
               "options": {"num_ctx": session.MAX_NUM_CTX, **(options or {})}}
    if placement == "top":
        payload["think"] = think
    else:
        payload["options"]["think"] = think
    if fmt is not None:
        payload["format"] = fmt
    started = time.monotonic()
    r = requests.post(f"{OLLAMA}/api/chat", json=payload, timeout=1800)
    r.raise_for_status()
    body = r.json()
    message = body.get("message", {})
    return {
        "seconds": round(time.monotonic() - started, 2),
        "prompt_tokens": body.get("prompt_eval_count"),
        "completion_tokens": body.get("eval_count"),
        "thinking_chars": len(message.get("thinking") or ""),
        "thinking": message.get("thinking") or "",
        "content": message.get("content") or "",
        "done_reason": body.get("done_reason"),
    }


def build_nuru_model() -> tuple[str, str]:
    """(re)build Nuru's model from its family's Modelfile, as Session.start does; (model, base)."""
    from core import session

    session.create_model(session.MODEL, session.BASE_MODEL, session._modelfile_for(session.BASE_MODEL))
    return session.MODEL, session.BASE_MODEL


def save(name: str, data) -> Path:
    RUNS.mkdir(exist_ok=True)
    path = RUNS / f"{name}-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(data, indent=1, default=str))
    return path
