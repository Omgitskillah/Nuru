"""Turns what a chat turn produces into the markdown the UI shows.

Kept free of Qt so it can be tested without a display.
"""

import re
from dataclasses import dataclass

from core.session import Event, Failure, RetryWithoutThinking, Text, Thinking, ToolCall

# A thematic break. The blank lines around it matter: directly under a line of text, `---`
# would turn that line into a heading instead.
BREAK = "\n\n---\n\n"

# Longest a tool call is shown, so an argument like a whole note body can't swamp the reply.
TOOL_CALL_LIMIT = 80

FENCE = re.compile(r"^```(\w*)[ \t]*\n(.*?)(?:^```[ \t]*$|\Z)", re.S | re.M)


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
        elif isinstance(event, ToolCall | Thinking | RetryWithoutThinking):
            # A call, the reasoning behind it and the note that a silent call is being retried are
            # shown above the reply, not in it. Ending the piece here still sets what the model said
            # before them apart from what it says after.
            self._growing = False
        elif isinstance(event, Failure):
            self.add_error(event.message)
        # ToolResult, Progress and LayaDecision are for the card above the reply, not the reply.

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
