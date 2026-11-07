"""Deterministic write, step and thinking safeguards, with configured tier/expert limits."""

from core.decision_config import DecisionConfig

from core.expert import ExpertPlanner, ExpertUnavailable, FoundNote, build_brief
from core.tier_policy import STOP, TierPolicy

__all__ = ["Enforcement", "STOP", "ExpertUnavailable"]

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
    def __init__(self, config: DecisionConfig, think_map: dict | None, planner: ExpertPlanner | None):
        self.config = config
        self.planner = planner
        # The model's tier -> Ollama `think` map (its profile, core.model_profile). A model without
        # one can't have its thinking level enforced; it stays as configured.
        self.think_map = think_map

    def on(self, point: str) -> bool:
        return self.config.mode(point) == "enforce"

    @property
    def any(self) -> bool:
        return self.tiers_enforced or any(self.on(point) for point in self.config.decision_points)

    @property
    def tiers_enforced(self) -> bool:
        return self.config.tiers.enforce and self.think_map is not None

    @property
    def step_cap(self) -> int:
        return self.config.tool_gate.step_cap

    def new_task(self) -> TierPolicy:
        return TierPolicy(self.config.tiers.order)

    def expert_available(self) -> bool:
        return self.planner is not None and self.planner.available()

    def think_for(self, policy: TierPolicy, failure: bool, current_think):
        """(Ollama think value, tier) for the next model call; (STOP, STOP) when the task must stop.
        Without tier enforcement: the session's own setting, and no tier."""
        if not self.tiers_enforced:
            return current_think, None
        proposed = self.config.tiers.default
        tier = policy.next(proposed, failure, self.expert_available())
        return self._think(tier, current_think)

    def at_step_cap(self, policy: TierPolicy, current_think):
        """The cap was hit: the expert's plan when config allows and the expert is there, else STOP -
        which the session turns into one last answer without tools."""
        if not self.config.expert.at_step_cap:
            return STOP, STOP
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
