"""The thinking-tier policy for one task (one turn), enforced by the harness when assess_effort is set
to enforce. Pure logic, no I/O, so every rule is a unit test:

- the tier for a step is the one assess_effort returned - its prediction if that cleared the tier's
  threshold, the configured default otherwise (the decision layer already applied that);
- within a task the tier only goes up, never back down;
- a failure signal (a cannot_do, the same schema failing validation twice, empty content, a failed
  check_claim) moves the tier up one step, whatever the prediction says;
- the step after the highest local tier is `expert`, if today's expert budget allows; otherwise
  the task stops and the user is told plainly what happened;
- hitting the step cap: one expert attempt if the budget allows, otherwise stop.
"""

from dataclasses import dataclass, field

STOP = "stop"


@dataclass
class TierPolicy:
    order: tuple[str, ...]  # cheapest first, `expert` last - config tiers.order
    current: str | None = None
    history: list[str] = field(default_factory=list)
    expert_attempted: bool = False

    def _rank(self, tier: str) -> int:
        return self.order.index(tier)

    def _up_one(self, tier: str) -> str:
        return self.order[min(self._rank(tier) + 1, len(self.order) - 1)]

    def next(self, proposed: str, failure: bool, expert_available: bool) -> str:
        """The tier for the next model call, or STOP."""
        tier = proposed
        if self.current is not None:
            if self._rank(tier) < self._rank(self.current):
                tier = self.current  # never back down
            if failure:
                tier = max(tier, self._up_one(self.current), key=self._rank)
        return self._settle(tier, expert_available)

    def at_step_cap(self, expert_available: bool) -> str:
        """The cap was hit: one expert attempt if the budget allows, else STOP."""
        if self.expert_attempted:
            return STOP
        return self._settle("expert", expert_available)

    def _settle(self, tier: str, expert_available: bool) -> str:
        if tier == "expert":
            if not expert_available or self.expert_attempted:
                return STOP
            self.expert_attempted = True
        self.current = tier
        self.history.append(tier)
        return tier
