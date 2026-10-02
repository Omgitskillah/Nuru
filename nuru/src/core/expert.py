"""The expert tier: Claude plans, the local model executes the plan inside the same gated loop.

What leaves the machine is the brief, and only the brief: the user's request, the tool names and
their argument schemas, the titles and IDs of notes that searches returned, and the steps so far as
tool names and statuses. Never a note's content - not a snippet, not a passage, not a summary of a
read. `build_brief` takes structured search results and pulls titles and IDs out of them itself, so
nothing it is handed can smuggle note text in; a test checks that no note text seen in a session
appears in the brief.

The API key is handed in by whoever builds the planner (Nuru's session: `expert_api_key()`), held in
memory only, and never logged. Calls are counted per local day in the decision log, against the
config's `expert.daily_cap`.
"""

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from laya_mcp.config import ExpertSettings
from laya_mcp.decision_log import DecisionLog

# Search tools whose results name notes; only their titles and IDs reach the brief.
SEARCH_TOOLS = frozenset({"search", "find_in_notes", "browse_notes", "notes_with_tag"})
# The expert's thinking is billed against max_tokens; too low a cap leaves the plan empty
# (deep-think hit exactly this at 1024).
_MAX_TOKENS = 16000
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

PLANNER_SYSTEM = (
    "You plan for a small local model that works in the user's Obsidian vault through tools. You "
    "cannot see the vault: you get the request, the tools, the titles and IDs of notes its searches "
    "found, and the steps it has taken. Reply with a short numbered plan - one tool call per step, "
    "naming the tool and the arguments to use (note IDs only from the list given) - and what the "
    "final reply should contain. If the request can't be done with these tools, say so in one line."
)


class ExpertUnavailable(Exception):
    """No plan: no key, today's cap reached, the API failed, or the request was declined."""


@dataclass(frozen=True)
class FoundNote:
    title: str
    id: str | None


def notes_found(tool: str, result_text: str) -> list[FoundNote]:
    """Titles and IDs of the notes a search returned - nothing else from its result."""
    if tool not in SEARCH_TOOLS:
        return []
    try:
        found = json.loads(result_text)
    except (TypeError, ValueError):
        return []
    items = next((found[k] for k in ("matches", "notes") if isinstance(found, dict) and isinstance(found.get(k), list)), [])
    notes = []
    for item in items:
        if isinstance(item, dict):
            title = item.get("title") or (item.get("path") or "").rsplit("/", 1)[-1].removesuffix(".md")
            notes.append(FoundNote(str(title), item.get("id")))
    return notes


def build_brief(request: str, tools: list[dict], notes: list[FoundNote], steps: list[dict[str, str]]) -> str:
    """The whole message the expert sees. `steps` are {tool, status}; anything else in them is ignored."""
    tool_lines = [
        f"- {t['function']['name']}: {json.dumps(t['function'].get('parameters', {}).get('properties', {}), sort_keys=True)}"
        for t in tools
    ]
    unique = list(dict.fromkeys(notes))
    note_lines = [f"- {n.title}" + (f" (id {n.id})" if n.id else " (no id)") for n in unique] or ["- (none yet)"]
    step_lines = [f"{i}. {s['tool']} - {s['status']}" for i, s in enumerate(steps, 1)] or ["(none yet)"]
    return "\n".join([
        f"Request: {request}", "",
        "Tools (name: argument schema):", *tool_lines, "",
        "Notes the searches found (titles and IDs only):", *note_lines, "",
        "Steps so far:", *step_lines,
    ])


class ExpertPlanner:
    def __init__(self, settings: ExpertSettings, log: DecisionLog, client: Any = None, api_key: str | None = None):
        self._settings = settings
        self._log = log
        self._client = client
        self._api_key = api_key

    def calls_today(self) -> int:
        return self._log.expert_calls_on(date.today().isoformat())

    def available(self) -> bool:
        has_key = self._client is not None or bool(self._api_key)
        return has_key and self.calls_today() < self._settings.daily_cap

    def plan(self, brief: str) -> str:
        if self.calls_today() >= self._settings.daily_cap:
            raise ExpertUnavailable(f"today's expert budget ({self._settings.daily_cap} calls) is used up")
        client = self._client or self._build_client()
        import anthropic

        try:
            response = client.beta.messages.create(
                model=self._settings.model,
                max_tokens=_MAX_TOKENS,
                betas=[_FALLBACK_BETA],
                fallbacks="default",
                system=PLANNER_SYSTEM,
                messages=[{"role": "user", "content": brief}],
            )
        except anthropic.APIError as e:
            self._log.record_expert_call(self._settings.model, "error", False)
            raise ExpertUnavailable(f"the expert call failed: {type(e).__name__}") from e
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        ok = response.stop_reason != "refusal" and bool(text)
        self._log.record_expert_call(self._settings.model, str(response.stop_reason), ok)
        if response.stop_reason == "refusal":
            raise ExpertUnavailable("the expert declined the request")
        if not text:
            raise ExpertUnavailable(f"the expert returned no plan (stop reason {response.stop_reason})")
        return text

    def _build_client(self):
        import anthropic

        if not self._api_key:
            raise ExpertUnavailable("no API key is configured for the expert")
        return anthropic.Anthropic(api_key=self._api_key)
