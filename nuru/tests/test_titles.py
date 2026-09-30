from core import titles

PROMPT = "What's my expected battery life with periodic attach on the area monitor?"


def test_clean_takes_the_json_title_and_strips_quotes_and_prefixes():
    assert titles.clean('{"title": "Battery life estimate"}') == "Battery life estimate"
    assert titles.clean('Title: "Battery life estimate."') == "Battery life estimate"
    assert titles.clean("  Battery   life  ") == "Battery life"


def test_a_short_grounded_title_passes():
    assert titles.check("Area monitor battery life", PROMPT) == []
    assert titles.check("Batteries with periodic attach", PROMPT) == []  # battery / batteries


def test_a_title_about_something_else_fails_as_ungrounded():
    assert titles.check("Weekend hiking plans", PROMPT) == ["shares no key word with the prompt"]


def test_a_long_or_copied_or_empty_title_fails():
    assert "more than 8 words" in titles.check("the expected battery life with periodic attach on the area monitor", PROMPT)
    assert "the prompt copied, not summarised" in titles.check("What's my expected battery life", PROMPT)
    assert titles.check("", PROMPT) == ["empty"]


def test_a_prompt_without_key_words_can_be_titled_anything_short():
    assert titles.check("Greeting", "hi") == []


def test_the_fallback_is_the_prompt_cut_at_a_word():
    assert titles.fallback("hi there") == "hi there"
    cut = titles.fallback(PROMPT)
    assert cut.endswith("…") and len(cut) <= titles.FALLBACK_CHARS + 1 and PROMPT.startswith(cut[:-1])


def test_a_title_that_is_just_the_question_is_noted_not_failed():
    prompt = "Is my task set schedulable?"
    assert titles.check("Is my task set schedulable?", prompt) == []
    assert titles.notes("Is my task set schedulable", prompt) == ["same as the prompt"]
    assert titles.notes("Task set schedulability", prompt) == []
