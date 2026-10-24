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
