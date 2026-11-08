import itertools

from core.thinking_watch import ThinkingWatch, repeated_sentence
from core.decision_config import WatchSettings

SETTINGS = WatchSettings(check_every_tokens=100, budgets={"low": 300, "high": 1000}, repeat_min_chars=40,
                         repeat_count=3, repeat_calls=3)
FRESH = "The note lists five loads, so I add each current in turn and check the units as I go. "


_steps = itertools.count()


def fresh_text(tokens: int) -> str:
    """Varied reasoning of about `tokens` tokens - no sentence ever repeats, across calls too."""
    return "".join(f"Step {i}: the next load is {i * 7} microamps, which I add to the running total. "
                   for i in (next(_steps) for _ in range(tokens // 20)))


def test_a_loop_is_a_sentence_seen_three_times():
    loop = "So the average current is the sum divided by the period, which gives about five milliamps. "
    assert repeated_sentence(FRESH + loop * 2, 40, 3) is None
    assert repeated_sentence(FRESH + loop * 3, 40, 3).startswith("so the average current")


def test_short_repeated_phrases_are_not_a_loop():
    assert repeated_sentence("Yes. Yes. Yes. Yes. OK. OK. OK.", 40, 3) is None


def test_the_budget_fires_past_the_tiers_limit_and_only_once():
    watch = ThinkingWatch(SETTINGS, "low")
    signals = [watch.feed(fresh_text(120)) for _ in range(4)]
    fired = [s for s in signals if s]
    assert len(fired) == 1 and fired[0].kind == "budget" and fired[0].source == "code"
    assert fired[0].tokens > 300


def test_repetition_is_caught_before_the_budget():
    watch = ThinkingWatch(SETTINGS, "high")
    loop = "I should recompute the overlap between the radio and the sensor duty cycles first. " * 3
    signal = None
    for _ in range(3):
        signal = signal or watch.feed(loop)
    assert signal.kind == "repeating" and "recompute the overlap" in signal.detail


def test_watch_has_no_external_model_callback():
    import inspect
    assert list(inspect.signature(ThinkingWatch).parameters) == ["settings", "tier"]


def test_without_a_tier_there_is_no_budget():
    watch = ThinkingWatch(SETTINGS, "medium")
    assert watch.budget is None
    assert all(watch.feed(fresh_text(200)) is None for _ in range(10))


def test_progress_below_the_budget_produces_no_signal():
    watch = ThinkingWatch(SETTINGS, "high")
    assert watch.feed(fresh_text(150)) is None
