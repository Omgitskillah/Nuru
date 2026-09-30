"""Watching the model's reasoning as it streams, to catch overthinking and loops mid-call.

A small model can think for thousands of tokens on a step thinking off would have got right, or
go round the same paragraph until it hits its token limit - 8,192 tokens on test 7, a 94-times
repeated paragraph before that. A tool the model has to choose to call can't stop that: it is one
long generation. So the harness watches instead, every `check_every_tokens` of thinking, cheapest
check first:

1. code: past the tier's thinking budget;
2. code: one sentence (of `repeat_min_chars` or more) seen `repeat_count` times - a loop;
3. Laya (watch_thinking): the latest stretch reads as repeating, ready to act, or stuck for a
   lookup - what code can't see.

The first signal in a call is what counts. The session decides what happens with it: in shadow
mode it is only logged; enforced, the call is stopped and the model asked again, thinking off
(core.enforcement.STEER). Token counts are estimated at four characters a token.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from laya_mcp.config import WatchSettings

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


@dataclass(frozen=True)
class WatchSignal:
    kind: str  # budget | repeating | ready | stuck
    source: str  # code | laya
    tokens: int  # thinking tokens (estimated) when it fired
    detail: str = ""


def estimate_tokens(text: str) -> int:
    return len(text) // 4


def repeated_sentence(text: str, min_chars: int, count: int) -> str | None:
    """The first sentence of at least `min_chars` characters that appears `count` times, if any."""
    seen: dict[str, int] = {}
    for raw in _SENTENCE.findall(text):
        sentence = " ".join(raw.split()).lower()
        if len(sentence) >= min_chars:
            seen[sentence] = seen.get(sentence, 0) + 1
            if seen[sentence] >= count:
                return sentence
    return None


class ThinkingWatch:
    """One model call's watch. `ask_laya(tail)` returns watch_thinking's answer (or None)."""

    def __init__(self, settings: WatchSettings, tier: str | None, ask_laya: Callable[[str], dict | None] | None):
        self.settings = settings
        self.budget = settings.budgets.get(tier) if tier else None
        self.ask_laya = ask_laya
        self.text = ""
        self.first: WatchSignal | None = None
        self._next_check = settings.check_every_tokens

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)

    def feed(self, chunk: str) -> WatchSignal | None:
        """Add a piece of thinking. Returns a signal the first time one fires, else None."""
        self.text += chunk
        if self.first is not None or self.tokens < self._next_check:
            return None
        self._next_check = self.tokens + self.settings.check_every_tokens
        self.first = self._check()
        return self.first

    def _check(self) -> WatchSignal | None:
        tokens = self.tokens
        if self.budget is not None and tokens > self.budget:
            return WatchSignal("budget", "code", tokens, f"over the {self.budget}-token budget")
        repeated = repeated_sentence(self.text, self.settings.repeat_min_chars, self.settings.repeat_count)
        if repeated:
            return WatchSignal("repeating", "code", tokens, repeated[:120])
        if self.ask_laya is not None:
            answer = self.ask_laya(self.text[-self.settings.tail_chars:])
            if answer and answer.get("signal") not in (None, "progressing"):
                return WatchSignal(answer["signal"], "laya", tokens)
        return None
