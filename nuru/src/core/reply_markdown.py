"""Turns what a chat turn produces into the markdown the UI shows.

Kept free of Qt so it can be tested without a display.
"""

import re
from dataclasses import dataclass

from core.session import Event, Text, ToolCall

# A thematic break. The blank lines around it matter: directly under a line of text, `---`
# would turn that line into a heading instead.
BREAK = "\n\n---\n\n"

# Longest a tool call is shown, so an argument like a whole note body can't swamp the reply.
TOOL_CALL_LIMIT = 80

# Opens a tool-call line, so the UI can tell it from the model's own text and show it small and
# light: Nuru's working, not part of its answer. A private-use character, so it shows up in nobody's
# text; Reply keeps it out of everything else. (Not a control character: Python calls those whitespace,
# and strip() would eat it.)
TOOL_MARK = "\ue000"

FENCE = re.compile(r"^```(\w*)[ \t]*\n(.*?)(?:^```[ \t]*$|\Z)", re.S | re.M)


def escape_md(text: str) -> str:
    """Backslash-escape markdown syntax so `text` is shown literally."""
    return re.sub(r"([\\`*_{}\[\]<>#|~])", r"\\\1", text)


def format_tool_call(name: str, arguments: dict) -> str:
    """A tool call as one marked line of inline code, cut short if it's long."""
    call = f"{name}({arguments})"
    if len(call) > TOOL_CALL_LIMIT:
        call = call[: TOOL_CALL_LIMIT - 1] + "…"
    call = call.replace("`", "'")  # a backtick would end the code span early
    return f"{TOOL_MARK}`{call}`"


class Reply:
    """One turn's reply as a single markdown document.

    The model's text and its tool calls are kept in the order they happen, and set apart by
    thematic breaks where the CLI prints its `[nuru]` and `[tool]` labels.
    """

    def __init__(self) -> None:
        self._pieces: list[str] = []
        self._growing = False  # the last piece is model text that more chunks may extend

    def add(self, event: Event) -> None:
        if isinstance(event, Text):
            text = event.text.replace(TOOL_MARK, "")  # only tool calls get to carry the mark
            if self._growing:
                self._pieces[-1] += text
            else:
                self._pieces.append(text)
                self._growing = True
        elif isinstance(event, ToolCall):
            self._add_piece(format_tool_call(event.name, event.arguments))
        else:
            self.add_error(event.message)

    def add_error(self, message: str) -> None:
        message = message.replace(TOOL_MARK, "")
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
