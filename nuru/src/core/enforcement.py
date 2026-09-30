"""What the session does with the decision layer's answers when a decision point is set to enforce.

Each decision point has its own flag in config/decision_layer.yaml, and the config refuses to load
`enforce` for any point whose thresholds haven't been calibrated (points decided by code alone have
none). A point in `shadow` changes nothing: the session asks, logs and carries on as it always has;
one that is `off` isn't asked. Enforced, per point:

- step_cap (code): a turn stops after tool_gate.step_cap rounds of tool calls.
- write_checks (code): a write code objects to (an invented note ID, a blocked tool, a protected
  path) doesn't run.
- watch_thinking (code): the watch's budget and repetition signals stop a call; repeated identical
  tool calls stop the turn. watch_laya: the watch's Laya signal stops a call too.
- tool_gate: for a read or write request the harness runs the first search itself (the request as
  its query) before the model starts; `answer` / `ask_user` calls the model without tools; after a
  tool result, `answer` / `ask_user` does the same.
- assess_effort: the thinking level of every model call comes from the tier policy
  (core.tier_policy), with escalation to the expert (core.expert) past the highest local tier.
- gate_write: a write's arguments are forced through a schema (core.write_args), then gate_write
  decides: proceed, ask the user (blocked when nobody is there to ask), or block.
- check_claim: the reply is held back until checked; a failed one is regenerated once with the
  log in view, and shown with a warning if it fails again.
- rerank: semantic_search results the model sees are Laya's reranked top k.

Everything that can be decided in code - which tools exist, what may be written where, which note
IDs are real - is decided before any of this, and stays so in shadow mode too.
"""

from laya_mcp.config import DecisionConfig

from core.expert import ExpertPlanner, ExpertUnavailable, FoundNote, build_brief
from core.tier_policy import STOP, TierPolicy

__all__ = ["Enforcement", "STOP", "ExpertUnavailable"]

REGENERATE = (
    "[Nuru] Your reply said something the log of what actually happened doesn't support: {failures}. "
    "What actually happened:\n{log}\nWrite the reply again. Say only what the log shows - including "
    "any change that failed or wasn't made."
)
WARNING = "\n\n---\n**Check this reply:** it may claim something that didn't happen ({failures}). What actually happened:\n{log}"
UNVERIFIED = "\n\n_(Unverified: the reply check couldn't run.)_"
STOPPED = "I stopped here: {reason}.\n\nWhat happened:\n{log}"
# watch_thinking enforced: the model's reasoning was stopped; it is asked again, thinking off.
STEER = {
    "stuck": "[Nuru] Stop reasoning now. Look up what you need with a tool, then answer.",
    "default": "[Nuru] Stop reasoning now - you have thought this through. Make your next tool call, "
               "or give your reply, directly.",
}


def log_lines(executed: list[dict]) -> str:
    """The execution log as plain lines, for the model to read or the user to see."""
    if not executed:
        return "- nothing was run"
    return "\n".join(f"- {e['tool']}: {'ok' if e['ok'] else 'FAILED'} - {e.get('result', '')}" for e in executed)


class Enforcement:
    def __init__(self, config: DecisionConfig, base_model: str, planner: ExpertPlanner | None):
        self.config = config
        self.planner = planner
        # A model with no tier map can't have its thinking level enforced; it stays as configured.
        self.think_map = config.tiers.models.get(base_model)

    def on(self, point: str) -> bool:
        return self.config.mode(point) == "enforce"

    @property
    def any(self) -> bool:
        return any(self.on(point) for point in self.config.decision_points)

    @property
    def tiers_enforced(self) -> bool:
        return self.on("assess_effort") and self.think_map is not None

    @property
    def step_cap(self) -> int:
        return self.config.tool_gate.step_cap

    def new_task(self) -> TierPolicy:
        return TierPolicy(self.config.tiers.order)

    def expert_available(self) -> bool:
        return self.planner is not None and self.planner.available()

    # ------------------------------------------------------------------ tool_gate
    def first_tool(self, gate: dict | None) -> str | None:
        """The read the harness runs itself before the model starts, if any."""
        return gate.get("first_tool") if self.on("tool_gate") and gate else None

    def offer_tools(self, gate: dict | None) -> bool:
        """Whether the next model call gets tools: not when the gate said answer or ask the user."""
        if not self.on("tool_gate") or not gate:
            return True
        return (gate.get("mode") or gate.get("next")) not in ("answer", "ask_user")

    # ------------------------------------------------------------------ assess_effort
    def think_for(self, policy: TierPolicy, effort: dict | None, failure: bool, current_think):
        """(Ollama think value, tier) for the next model call; (STOP, STOP) when the task must stop.
        Without tier enforcement: the session's own setting, and no tier."""
        if not self.tiers_enforced:
            return current_think, None
        proposed = effort["tier"] if effort else self.config.tiers.default
        tier = policy.next(proposed, failure, self.expert_available())
        return self._think(tier, current_think)

    def at_step_cap(self, policy: TierPolicy, current_think):
        return self._think(policy.at_step_cap(self.expert_available()), current_think)

    def _think(self, tier: str, current_think):
        if tier == STOP:
            return STOP, STOP
        if self.think_map is None:
            return current_think, tier
        # The expert plans; the local model carries the plan out at its highest local tier.
        local = tier if tier != "expert" else self.config.tiers.order[-2]
        return self.think_map[local], tier

    def expert_plan(self, request: str, tools: list[dict], notes: list[FoundNote], steps: list[dict]) -> str:
        if self.planner is None:
            raise ExpertUnavailable("no expert is configured")
        return self.planner.plan(build_brief(request, tools, notes, steps))
