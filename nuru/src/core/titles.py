"""A conversation's title: the local model's summary of its first prompt, checked in code.

The window's history panel lists conversations by title. The model writes one, thinking off and
held to a JSON schema, and code checks it before it's used: one line, at most TITLE_MAX_WORDS
words and TITLE_MAX_CHARS characters, not simply the prompt copied out, and grounded - it shares
at least one content word with the prompt, so a title about something the prompt never mentions
fails. A title that fails is replaced with the prompt itself, cut short (`fallback`), and what
the check found is stored with it, so the model's summarising can be measured later.
"""

import json
import re

TITLE_MAX_CHARS = 60
TITLE_MAX_WORDS = 8
FALLBACK_CHARS = 48

TITLE_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string", "maxLength": TITLE_MAX_CHARS}},
    "required": ["title"],
}

TITLE_PROMPT = (
    "Write a short title, 3 to 6 words, for a chat that starts with the message below. Use the "
    "message's own key words; don't answer it, and don't add anything it doesn't say. Reply with "
    'JSON: {{"title": "..."}}.\n\nMessage:\n{prompt}'
)

_WORD = re.compile(r"[A-Za-z0-9ÄÖÜäöüß]+")
_STOP = frozenset(
    "about after again also been before could does each from have into just like make many more most much "
    "must only over some such than that their them then there these they this those very what when where "
    "which while will with would your yours please tell show give find know".split()
)


def content_words(text: str) -> set[str]:
    """Words of four letters or more, lowercased and cut to their first five letters (so
    "battery" and "batteries" match), minus common filler."""
    return {w.lower()[:5] for w in _WORD.findall(text) if len(w) >= 4 and w.lower() not in _STOP}


def clean(raw: str) -> str:
    """The model's title as it would be shown: the JSON's `title` if it answered in JSON, without
    quotes, a "Title:" prefix, a trailing full stop or extra whitespace."""
    text = raw.strip()
    try:
        text = str(json.loads(text).get("title", ""))
    except (ValueError, AttributeError):
        pass
    text = re.sub(r"^\s*title\s*:\s*", "", text, flags=re.I)
    text = " ".join(text.split()).strip("\"'“”‘’`*").rstrip(".").strip()
    return text


def check(title: str, prompt: str) -> list[str]:
    """What's wrong with `title` as a summary of `prompt`; empty if nothing."""
    problems = []
    if not title:
        return ["empty"]
    if len(title) > TITLE_MAX_CHARS:
        problems.append(f"longer than {TITLE_MAX_CHARS} characters")
    if len(title.split()) > TITLE_MAX_WORDS:
        problems.append(f"more than {TITLE_MAX_WORDS} words")
    prompt_text = " ".join(prompt.split())
    if len(prompt_text) > len(title) + 10 and prompt_text.lower().startswith(title.lower()):
        problems.append("the prompt copied, not summarised")
    words = content_words(prompt)
    if words and not content_words(title) & words:
        problems.append("shares no key word with the prompt")
    return problems


def notes(title: str, prompt: str) -> list[str]:
    """Observations about a title that passed, kept to measure the model's summarising: a short
    question often comes back as itself - fine as a title, but not a summary."""
    def plain(text: str) -> str:
        return " ".join(text.lower().split()).rstrip("?.! ")

    return ["same as the prompt"] if title and plain(title) == plain(prompt) else []


def fallback(prompt: str) -> str:
    """The first prompt itself, cut at a word boundary to about FALLBACK_CHARS characters."""
    text = " ".join(prompt.split())
    if len(text) <= FALLBACK_CHARS:
        return text
    cut = text[:FALLBACK_CHARS].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"
