"""Questions Nuru puts to the user: yes/no, one of several options, or any of several (checkboxes),
optionally with a recommended option.

Two kinds of question share this shape. Code raises some mid-turn and waits for the answer (a write
that needs the user's OK, a save the user's message ruled out); Session.answer_question gets those.
The model can ask one with the ask_user tool, which ends its turn; the answer is the next message.

Kept free of Qt so the CLI and tests can use it.
"""

import json
import re
from dataclasses import dataclass

YES_NO, SINGLE, MULTI = "yes_no", "single", "multi"
YES, NO = "Yes", "No"
MAX_OPTIONS = 6
# How long a question code raises mid-turn waits for an answer; none in time is taken as No.
ANSWER_SECONDS = 10

TOOL_NAME = "ask_user"
TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Ask the user one question and end your turn; their answer comes as the next message. Use it only "
            "when you can't go on without the user's choice: two readings of the request lead to different "
            "results, or the request needs a decision only they can make. Never ask for what the notes or a "
            "calculation can tell you. kind: yes_no, single (pick one option) or multi (pick any). Mark at most "
            "one option recommended, and say why in its description, from the notes or your results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question, in one or two sentences."},
                "kind": {"type": "string", "enum": [YES_NO, SINGLE, MULTI]},
                "options": {
                    "type": "array",
                    "description": "2-6 options for single and multi; leave out for yes_no.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "description": {"type": "string"},
                            "recommended": {"type": "boolean"},
                        },
                        "required": ["label"],
                    },
                },
                "recommended": {"type": "string", "enum": [YES, NO], "description": "yes_no only: the answer you recommend."},
            },
            "required": ["question", "kind"],
        },
    },
}


@dataclass(frozen=True)
class Option:
    label: str
    description: str = ""
    recommended: bool = False


@dataclass(frozen=True)
class Question:
    text: str
    kind: str = YES_NO
    options: tuple[Option, ...] = ()
    details: str = ""  # shown under the question, e.g. the write that is waiting
    allow_other: bool = False  # the user may type their own answer instead
    timeout: float | None = None  # seconds to wait for an answer before it is taken as No

    @property
    def choices(self) -> tuple[Option, ...]:
        return self.options if self.kind != YES_NO or self.options else (Option(YES), Option(NO))

    @property
    def recommended(self) -> Option | None:
        return next((o for o in self.choices if o.recommended), None)


def yes_no(text: str, *, recommend: str | None = None, details: str = "", timeout: float | None = None) -> Question:
    return Question(text, YES_NO, tuple(Option(label, recommended=label == recommend) for label in (YES, NO)), details,
                    timeout=timeout)


def from_tool(arguments) -> Question:
    """ask_user's arguments as a Question. Raises ValueError, worded for the model, if they don't make one."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError as e:
            raise ValueError(f"arguments aren't JSON: {e}") from None
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    text, kind = str(arguments.get("question") or "").strip(), arguments.get("kind")
    if not text:
        raise ValueError("question is empty")
    if kind not in (YES_NO, SINGLE, MULTI):
        raise ValueError(f"kind must be {YES_NO}, {SINGLE} or {MULTI}")
    if kind == YES_NO:
        recommend = arguments.get("recommended")
        if recommend not in (None, YES, NO):
            raise ValueError("recommended must be Yes or No")
        return yes_no(text, recommend=recommend)
    raw = arguments.get("options")
    if not isinstance(raw, list) or not 2 <= len(raw) <= MAX_OPTIONS:
        raise ValueError(f"{kind} needs 2-{MAX_OPTIONS} options")
    options = []
    for item in raw:
        item = {"label": item} if isinstance(item, str) else item
        label = str(item.get("label") or "").strip() if isinstance(item, dict) else ""
        if not label:
            raise ValueError("every option needs a label")
        options.append(Option(label, str(item.get("description") or "").strip(), item.get("recommended") is True))
    if len({o.label.lower() for o in options}) != len(options):
        raise ValueError("option labels must differ")
    if sum(o.recommended for o in options) > 1:
        raise ValueError("recommend at most one option")
    return Question(text, kind, tuple(options), allow_other=True)


def as_text(question: Question) -> str:
    """The question as plain markdown: what the conversation keeps, and what a reader without the
    buttons sees."""
    lines = [question.text]
    if question.kind != YES_NO or question.recommended:
        lines.append("")
        for option in question.choices:
            line = f"- {option.label}" + (" (recommended)" if option.recommended else "")
            lines.append(line + (f": {option.description}" if option.description else ""))
    return "\n".join(lines)


def answer_text(chosen: list[str]) -> str:
    """The user's answer as the message that goes back to the model."""
    return "; ".join(chosen) if chosen else "None of these."


def is_yes(chosen: list[str] | None) -> bool:
    return chosen == [YES]


# --------------------------------------------------------------------------- questions code raises
# Each waits ANSWER_SECONDS for an answer, mid-turn; none in time is taken as No.
def write(tool: str, arguments: dict) -> Question:
    """gate_write wants the user's OK before a write runs."""
    return yes_no(f"Nuru wants to run {tool} on {_target(arguments)}. Allow it?",
                  details=_details(arguments), timeout=ANSWER_SECONDS)


def against_no_save(tool: str, arguments: dict, said: str) -> Question:
    """A write in a turn whose message ruled writes out."""
    return yes_no(f'Your message said "{said}", but Nuru wants to run {tool} on {_target(arguments)}. Go ahead anyway?',
                  recommend=NO, details=_details(arguments), timeout=ANSWER_SECONDS)


def shared_write(tool: str, arguments: dict, path: str) -> Question:
    """A write that lands in shared/, which other people read."""
    return yes_no(f"Nuru wants to run {tool} on {path}, where others can read it. Allow it?",
                  details=_details(arguments), timeout=ANSWER_SECONDS)


def unknown_write(tool: str, arguments: dict, why: str) -> Question:
    """A write whose note couldn't be looked up, so it may land in shared/."""
    return yes_no(f"Nuru couldn't check where {tool} on {_target(arguments)} would land ({why}), so it may be in "
                  "shared/, where others can read it. Allow it?", details=_details(arguments), timeout=ANSWER_SECONDS)


def _details(arguments: dict) -> str:
    return json.dumps(arguments, ensure_ascii=False, indent=1)[:2000]


def _target(arguments: dict) -> str:
    return str(arguments.get("note") or arguments.get("source_path") or "a new note")


# Telling Nuru not to write. Every match in the eval prompts (legacy, multi-turn, failure pairs; 41 turns,
# checked 2026-10-09) is a turn whose user forbids writes; none of them is a request to write.
_NO_SAVE = re.compile(
    r"\b(?:do not|don't|dont|never|no need to)\s+(?:save|write|store|edit|change|modify|update|touch)\b[^.;:\n]*"
    r"|\bno (?:edits|changes|writes)\b"
    r"|\bwithout (?:saving|writing|editing|changing)\b[^.;:\n]*",
    re.IGNORECASE,
)


def no_save_phrase(text: str) -> str | None:
    """The words in `text` that rule out writing, if any."""
    match = _NO_SAVE.search(text or "")
    return match.group(0).strip() if match else None
