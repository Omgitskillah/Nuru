import itertools

from core.thinking_watch import ThinkingWatch, repeated_sentence
from laya_mcp.config import WatchSettings

SETTINGS = WatchSettings(check_every_tokens=100, budgets={"low": 300, "high": 1000}, repeat_min_chars=40,
                         repeat_count=3, tail_chars=400, repeat_calls=3)
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
    watch = ThinkingWatch(SETTINGS, "low", None)
    signals = [watch.feed(fresh_text(120)) for _ in range(4)]
    fired = [s for s in signals if s]
    assert len(fired) == 1 and fired[0].kind == "budget" and fired[0].source == "code"
    assert fired[0].tokens > 300


def test_repetition_is_caught_before_the_budget():
    watch = ThinkingWatch(SETTINGS, "high", None)
    loop = "I should recompute the overlap between the radio and the sensor duty cycles first. " * 3
    signal = None
    for _ in range(3):
        signal = signal or watch.feed(loop)
    assert signal.kind == "repeating" and "recompute the overlap" in signal.detail


def test_laya_is_asked_only_at_checks_and_only_with_the_tail():
    asked = []

    def ask(tail):
        asked.append(tail)
        return {"signal": "ready"} if len(asked) == 2 else {"signal": "progressing"}

    watch = ThinkingWatch(SETTINGS, "high", ask)
    signals = [watch.feed(fresh_text(60)) for _ in range(8)]
    assert [s.kind for s in signals if s] == ["ready"]
    assert len(asked) == 2 and all(len(t) <= 400 for t in asked)


def test_without_a_tier_there_is_no_budget():
    watch = ThinkingWatch(SETTINGS, "medium", lambda tail: None)
    assert watch.budget is None
    assert all(watch.feed(fresh_text(200)) is None for _ in range(10))


def test_laya_down_leaves_only_the_code_checks():
    watch = ThinkingWatch(SETTINGS, "high", lambda tail: None)
    assert watch.feed(fresh_text(150)) is None
