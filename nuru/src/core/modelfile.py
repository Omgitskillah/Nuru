"""Render family headers with the shared assistant prompt before an Ollama build.

system.md annotates tool references as {{tool:name}}. They render to the original
name, allowing contract checks without guessing which prose words name tools.
"""

import re
from pathlib import Path

from core.tool_contract import MODEL_TOOLS, RETIRED_TOOLS

SYSTEM_PLACEHOLDER = "__SYSTEM_PROMPT__"
BASE_MODEL_PLACEHOLDER = "__BASE_MODEL__"
_TOOL_REFERENCE = re.compile(r"\{\{tool:([a-z][a-z0-9_]*)\}\}")


def render_system(source: str) -> str:
    def tool_name(match: re.Match) -> str:
        name = match.group(1)
        if name not in MODEL_TOOLS:
            kind = "retired" if name in RETIRED_TOOLS else "unknown"
            raise ValueError(f"Assistant prompt references {kind} tool {name!r}")
        return name

    rendered = _TOOL_REFERENCE.sub(tool_name, source)
    if "{{tool:" in rendered:
        raise ValueError("Assistant prompt has a malformed {{tool:name}} reference")
    retired = sorted(set(re.findall(r"\b[a-z][a-z0-9_]*\b", rendered)) & RETIRED_TOOLS)
    if retired:
        raise ValueError(f"Assistant prompt references retired tools: {', '.join(retired)}")
    if '"""' in rendered:
        raise ValueError('Assistant prompt cannot contain the Modelfile delimiter """')
    return rendered


def render_modelfile(path: Path, base_model: str) -> str:
    """Resolve shared SYSTEM text and FROM; standalone inline prompts also work."""
    template = path.read_text(encoding="utf-8")
    if SYSTEM_PLACEHOLDER in template:
        if template.count(SYSTEM_PLACEHOLDER) != 1 or f'SYSTEM """{SYSTEM_PLACEHOLDER}"""' not in template:
            raise ValueError(f'{path}: use SYSTEM """{SYSTEM_PLACEHOLDER}""" exactly once')
        system = render_system((path.parent / "system.md").read_text(encoding="utf-8"))
        template = template.replace(SYSTEM_PLACEHOLDER, system)
    elif 'SYSTEM """' in template:
        # Older inline assistant files must not bypass retired-name validation.
        start, body = template.split('SYSTEM """', 1)
        system, end = body.rsplit('"""', 1)
        template = f'{start}SYSTEM """{render_system(system)}"""{end}'
    return template.replace(BASE_MODEL_PLACEHOLDER, base_model)


# ---------------------------------------------------------------------- a user's header (TODO F22)
# The part of a Modelfile a user may edit, checked strictly: Nuru's own files above are trusted, a
# user's text is not. The rendering is rebuilt from what was checked, never copied, so nothing a user
# writes reaches `ollama create` unparsed; and it always ends with Nuru's one SYSTEM, the bundled
# system.md. A header changes how the model samples and formats its prompt, not what tools may do:
# that stays with Nuru's tool checks.
HEADER_BYTES = 64 * 1024
GUARD_NUM_PREDICT = 8192  # what a reply may run to at most: Nuru's own headers' runaway guard
_WORD = re.compile(r"[A-Za-z]+")
_INT, _FLOAT = re.compile(r"-?\d{1,10}"), re.compile(r"-?\d{1,6}(?:\.\d{1,8})?")
_PARAMETERS = {  # name -> (type, least, most), as Ollama 0.40 takes them
    "temperature": (float, 0, 2), "top_p": (float, 0, 1), "min_p": (float, 0, 1), "top_k": (int, 0, 1000),
    "repeat_penalty": (float, 0, 2), "presence_penalty": (float, -2, 2), "frequency_penalty": (float, -2, 2),
    "repeat_last_n": (int, -1, 4096), "seed": (int, -1, 2**31 - 1), "num_ctx": (int, 512, 262144),
    "num_batch": (int, 1, 4096), "num_predict": (int, 1, GUARD_NUM_PREDICT), "stop": (str, 1, 64),
}
_STOPS = 8
CAPABILITIES = {"completion", "tools", "thinking", "vision", "insert"}
_REFUSED = {
    "SYSTEM": "Nuru's own prompt is always the system prompt",
    "MESSAGE": "a header can't add conversation turns",
    "ADAPTER": "a header can't load files from this computer",
    "LICENSE": "the base model's licence stays as it is",
}


class HeaderError(ValueError):
    """What is wrong with a user's header, and on which line."""

    def __init__(self, line: int, problem: str) -> None:
        super().__init__(f"line {line}: {problem}")
        self.line, self.problem = line, problem


def check_text(text: str, limit: int, what: str) -> None:
    """Text a user saved: at most `limit` bytes of UTF-8, no control characters but tab and newline."""
    if not isinstance(text, str):
        raise ValueError(f"{what} must be text")
    if len(text.encode("utf-8", "strict")) > limit:
        raise ValueError(f"{what} is longer than {limit // 1024} KiB")
    bad = next((c for c in text if (ord(c) < 32 and c not in "\t\n") or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF), None)
    if bad is not None:
        raise ValueError(f"{what} contains a control character (U+{ord(bad):04X})")


def parse_header(text: str) -> list[tuple[str, str, int, bool]]:
    """Ollama's Modelfile syntax: (DIRECTIVE, its argument, line, whether it was a \"\"\" block). A line
    starting with # is a comment; a directive is a case-insensitive word, its argument the rest of the
    line, or a \"\"\" block that may run over several lines."""
    if isinstance(text, str):
        text = text.replace("\r\n", "\n")  # as a Windows editor saves it
    check_text(text, HEADER_BYTES, "The header")
    lines, found, i = text.split("\n"), [], 0
    while i < len(lines):
        number, stripped = i + 1, lines[i].strip()
        i += 1
        if not stripped or stripped.startswith("#"):
            continue
        word, _, rest = stripped.replace("\t", " ").partition(" ")
        if not _WORD.fullmatch(word):
            raise HeaderError(number, f"{word[:40]!r} isn't a directive")
        rest = rest.strip()
        if not rest.startswith('"""'):
            found.append((word.upper(), rest, number, False))
            continue
        body = [rest[3:]]
        while '"""' not in body[-1]:
            if i == len(lines):
                raise HeaderError(number, 'a """ block is never closed')
            body.append(lines[i])
            i += 1
        last, after = body[-1].split('"""', 1)
        if after.strip():
            raise HeaderError(i if len(body) > 1 else number, 'nothing may follow a closing """')
        found.append((word.upper(), "\n".join([*body[:-1], last]), number, True))
    return found


def check_header(text: str, capabilities: set[str] | None = None) -> list[tuple[str, str]]:
    """The header's directives, checked: what `render_header` builds from. `capabilities`, the pulled
    base model's own (its /api/show), limits CAPABILITY to what that model really has."""
    checked, seen, stops, base = [], set(), 0, 0
    for name, value, line, block in parse_header(text):
        if name in _REFUSED:
            raise HeaderError(line, f"{name} isn't allowed: {_REFUSED[name]}")
        if block and name != "TEMPLATE":
            raise HeaderError(line, f'only TEMPLATE takes a """ block, not {name}')
        if name == "FROM":
            if value != BASE_MODEL_PLACEHOLDER:
                raise HeaderError(line, f"FROM must be {BASE_MODEL_PLACEHOLDER}: Nuru fills in the model")
            base += 1
            if base > 1:
                raise HeaderError(line, "FROM is given twice")
            continue
        if name == "TEMPLATE":
            if not block and len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]  # TEMPLATE "…" on one line
            if '"""' in value or value.startswith('"') or value.endswith('"'):
                raise HeaderError(line, 'a TEMPLATE can\'t contain """ or start or end with "')
            if name in seen:
                raise HeaderError(line, "TEMPLATE is given twice")
            seen.add(name)
            checked.append((name, value))
        elif name == "PARAMETER":
            key, _, raw = value.partition(" ")
            if key not in _PARAMETERS:
                raise HeaderError(line, f"unknown or unsupported parameter {key[:40]!r}")
            kind, least, most = _PARAMETERS[key]
            raw = raw.strip()
            if key == "stop":
                stops += 1
                if stops > _STOPS:
                    raise HeaderError(line, f"at most {_STOPS} stop sequences")
                if not (len(raw) >= 2 and raw[0] == raw[-1] == '"' and '"' not in raw[1:-1]
                        and least <= len(raw) - 2 <= most):
                    raise HeaderError(line, f'a stop sequence is "quoted", 1 to {most} characters')
            else:
                if key in seen:
                    raise HeaderError(line, f"{key} is given twice")
                seen.add(key)
                if not (_INT if kind is int else _FLOAT).fullmatch(raw) or not least <= kind(raw) <= most:
                    raise HeaderError(line, f"{key} must be {'a whole number' if kind is int else 'a number'} "
                                            f"from {least} to {most}")
            checked.append((name, f"{key} {raw}"))
        elif name == "CAPABILITY":
            if value not in CAPABILITIES or (capabilities is not None and value not in capabilities):
                raise HeaderError(line, f"the model doesn't have the capability {value[:40]!r}")
            if ("CAPABILITY", value) in checked:
                raise HeaderError(line, f"CAPABILITY {value} is given twice")
            checked.append((name, value))
        elif name == "REQUIRES":
            if not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}", value):
                raise HeaderError(line, "REQUIRES is an Ollama version such as 0.34.4")
            if name in seen:
                raise HeaderError(line, "REQUIRES is given twice")
            seen.add(name)
            checked.append((name, value))
        else:
            raise HeaderError(line, f"unknown directive {name[:40]}")
    if base != 1:
        raise HeaderError(1, f"the header needs FROM {BASE_MODEL_PLACEHOLDER}")
    return checked


def default_header(path: Path) -> str:
    """Nuru's checked-in header for a family, as a user starts editing it: without the trusted SYSTEM
    placeholder, which render_header puts back."""
    return path.read_text(encoding="utf-8").replace(f'SYSTEM """{SYSTEM_PLACEHOLDER}"""', "").rstrip() + "\n"


def render_header(text: str, base_model: str, system_md: Path) -> str:
    """The Modelfile for `ollama create`: FROM `base_model`, the checked directives, the num_predict
    guard if the header has none, and SYSTEM, `system_md` rendered and checked (render_system)."""
    if not base_model or any(c.isspace() or c == '"' for c in base_model):
        raise ValueError(f"not a model name: {base_model!r}")
    lines, checked = [f"FROM {base_model}"], check_header(text)
    for name, value in checked:
        lines.append(f'TEMPLATE """{value}"""' if name == "TEMPLATE" else f"{name} {value}")
    if not any(name == "PARAMETER" and value.startswith("num_predict ") for name, value in checked):
        lines.append(f"PARAMETER num_predict {GUARD_NUM_PREDICT}")
    lines.append(f'SYSTEM """{render_system(system_md.read_text(encoding="utf-8"))}"""')
    return "\n".join(lines) + "\n"
