from core.tier_policy import STOP, TierPolicy

ORDER = ("off", "low", "high", "expert")


def test_the_first_step_takes_the_proposed_tier():
    assert TierPolicy(ORDER).next("low", failure=False, expert_available=True) == "low"


def test_the_tier_never_goes_back_down():
    policy = TierPolicy(ORDER)
    policy.next("high", False, True)
    assert policy.next("off", False, True) == "high"


def test_a_failure_moves_up_one_whatever_was_proposed():
    policy = TierPolicy(ORDER)
    policy.next("off", False, True)
    assert policy.next("off", failure=True, expert_available=True) == "low"
    assert policy.next("off", failure=True, expert_available=True) == "high"


def test_a_failure_doesnt_undercut_a_higher_proposal():
    policy = TierPolicy(ORDER)
    policy.next("off", False, True)
    assert policy.next("high", failure=True, expert_available=True) == "high"


def test_after_high_comes_expert_when_the_budget_allows():
    policy = TierPolicy(ORDER)
    policy.next("high", False, True)
    assert policy.next("high", failure=True, expert_available=True) == "expert"


def test_after_high_without_budget_the_task_stops():
    policy = TierPolicy(ORDER)
    policy.next("high", False, False)
    assert policy.next("high", failure=True, expert_available=False) == STOP


def test_the_expert_is_tried_once_per_task():
    policy = TierPolicy(ORDER)
    assert policy.next("expert", False, True) == "expert"
    assert policy.next("expert", failure=True, expert_available=True) == STOP


def test_the_step_cap_gets_one_expert_attempt_then_stops():
    policy = TierPolicy(ORDER)
    policy.next("low", False, True)
    assert policy.at_step_cap(expert_available=True) == "expert"
    assert policy.at_step_cap(expert_available=True) == STOP
    assert TierPolicy(ORDER).at_step_cap(expert_available=False) == STOP


def test_history_records_every_tier_used():
    policy = TierPolicy(ORDER)
    policy.next("off", False, True)
    policy.next("off", True, True)
    assert policy.history == ["off", "low"]
