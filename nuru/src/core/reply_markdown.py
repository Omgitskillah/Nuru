"""Turns what a chat turn produces into the markdown the UI shows.

Kept free of Qt so it can be tested without a display.
"""

import re
from dataclasses import dataclass

from core.session import Ask, Event, RetryWithoutThinking, Text, Thinking, ThinkingChunk, ToolCall

# A thematic break. The blank lines around it matter: directly under a line of text, `---`
# would turn that line into a heading instead.
BREAK = "\n\n---\n\n"

# Longest a tool call is shown, so an argument like a whole note body can't swamp the reply.
TOOL_CALL_LIMIT = 80

FENCE = re.compile(r"^```(\w*)[ \t]*\n(.*?)(?:^```[ \t]*$|\Z)", re.S | re.M)


# Qt's markdown parser (6.11) loses everything after an unclosed <br>, and in a table that is every
# cell and row after it: models put <br> in table cells, and a 7-row audit table drew 378 of its
# 4,882 characters (2026-10-09). The self-closed <br/> it handles, as a line break, tables included.
_BR = re.compile(r"<br\s*>", re.IGNORECASE)
_CODE_SPAN = re.compile(r"(`+)(?:.*?)\1", re.S)


def qt_safe(text: str) -> str:
    """`text` with each <br> written <br/> for Qt's parser, except in inline code, where it is the
    model's literal text."""
    out, last = [], 0
    for span in _CODE_SPAN.finditer(text):
        out += [_BR.sub("<br/>", text[last:span.start()]), span.group(0)]
        last = span.end()
    return "".join(out) + _BR.sub("<br/>", text[last:])


def escape_md(text: str) -> str:
    """Backslash-escape markdown syntax so `text` is shown literally."""
    return re.sub(r"([\\`*_{}\[\]<>#|~])", r"\\\1", text)


def format_tool_call(name: str, arguments: dict) -> str:
    """A tool call as one line of plain text, cut short if it's long."""
    call = f"{name}({arguments})"
    if len(call) > TOOL_CALL_LIMIT:
        call = call[: TOOL_CALL_LIMIT - 1] + "…"
    return call


class Reply:
    """One turn's reply as a single markdown document.

    The model's text, in the order it was said and set apart by thematic breaks where the CLI
    prints its `[nuru]` label. The reasoning and the tool calls that fall between those pieces
    are the UI's to show, so they leave nothing here but the break around them.
    """

    def __init__(self) -> None:
        self._pieces: list[str] = []
        self._growing = False  # the last piece is model text that more chunks may extend

    def add(self, event: Event) -> None:
        if isinstance(event, Text):
            if self._growing:
                self._pieces[-1] += event.text
            else:
                self._pieces.append(event.text)
                self._growing = True
        elif isinstance(event, ToolCall | Thinking | ThinkingChunk | RetryWithoutThinking | Ask):
            # A call, the reasoning behind it and the note that a silent call is being retried are
            # shown above the reply, not in it; a question has its own card under it. Ending the piece
            # here still sets what the model said before them apart from what it says after.
            self._growing = False
        else:
            self.add_error(event.message)

    def add_error(self, message: str) -> None:
        self._add_piece("**Error:** " + escape_md(message).replace("\n", "\n\n"))

    def _add_piece(self, piece: str) -> None:
        self._pieces.append(piece)
        self._growing = False

    @property
    def markdown(self) -> str:
        """The reply so far. Breaks only go between pieces that have something in them, so the
        reply never starts or ends with one, and a model call with no text adds none."""
        return BREAK.join(piece.strip() for piece in self._pieces if piece.strip())


@dataclass(frozen=True)
class Prose:
    """Markdown to render as text."""

    text: str


@dataclass(frozen=True)
class Code:
    """A fenced code block."""

    language: str
    source: str


def split_fences(text: str) -> list[Prose | Code]:
    """Split markdown into the prose and the fenced code blocks in it.

    Qt paints block backgrounds without their margins, so fenced code can't be padded inside the
    markdown view; it gets its own widget instead. A fence that is still streaming counts as open.
    """
    parts: list[Prose | Code] = []
    pos = 0
    for match in FENCE.finditer(text):
        parts.append(Prose(text[pos : match.start()]))
        parts.append(Code(match.group(1), match.group(2).rstrip("\n")))
        pos = match.end()
    parts.append(Prose(text[pos:]))
    return [part for part in parts if isinstance(part, Code) or part.text.strip()]
