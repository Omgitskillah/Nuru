"""The eleven Obsidian template files generate_templates writes to Templates/.

Each type maps to a fixed filename and a fixed body (everything after the H1
heading). This is a lookup table, not logic: generate_templates in server.py
builds each file's frontmatter from frontmatter_template.py, then appends the
H1 and the body looked up here, verbatim.

Trailing spaces on placeholder lines (e.g. "- [stated] ") are intentional -
they're where a human's cursor lands when filling the line in - and must be
preserved exactly as written below, not stripped.
"""

# type -> filename, in the fixed 01-11 order these files are meant to sort in.
FILENAMES = {
    "profile": "01 Profile.md",
    "preferences": "02 Preferences.md",
    "project": "03 Project.md",
    "person": "04 Person.md",
    "topic": "05 Topic.md",
    "transcript": "06 Transcript.md",
    "session": "07 Session.md",
    "index": "08 Index.md",
    "directives": "09 Directives.md",
    "daily": "10 Daily.md",
    "correction": "11 Correction.md",
}

# The H1 for every type is "# {{title}}", except daily, which uses the date
# instead of a title.
DAILY_TYPE = "daily"

# type -> body text (no leading/trailing newline; generate_templates adds the
# surrounding blank lines and the final newline).
BODIES = {
    "profile": (
        "## Identity\n"
        "\n"
        "- [stated] Name: \n"
        "- [stated] Role: \n"
        "- [stated] Employer: \n"
        "- [stated] Based in: \n"
        "\n"
        "## Works on\n"
        "\n"
        "- [stated] "
    ),
    "preferences": (
        "## Response style\n"
        "\n"
        "- [stated] \n"
        "\n"
        "## Format\n"
        "\n"
        "- [stated] \n"
        "\n"
        "## Skip\n"
        "\n"
        "- [stated] "
    ),
    "project": (
        "## Goal\n"
        "\n"
        "\n"
        "## Constraints\n"
        "\n"
        "\n"
        "## Current state\n"
        "\n"
        "\n"
        "## Blocked on\n"
        "\n"
        "- \n"
        "\n"
        "## Decisions\n"
        "\n"
        "- [stated] {{date}} — "
    ),
    "person": (
        "## Relationship\n"
        "\n"
        "- [stated] \n"
        "\n"
        "## Context\n"
        "\n"
        "- [stated] "
    ),
    "topic": (
        "## Facts\n"
        "\n"
        "- [stated] \n"
        "- [inferred] "
    ),
    "transcript": (
        "## Session\n"
        "\n"
        "- Date: {{date}}\n"
        "- Started: {{time}}\n"
        "\n"
        "## Turns\n"
        "\n"
        "**Clarence:** \n"
        "\n"
        "**Nuru:** "
    ),
    "session": (
        "## Open questions\n"
        "\n"
        "- \n"
        "\n"
        "## Decided\n"
        "\n"
        "- {{date}} — "
    ),
    "index": (
        "## Notes\n"
        "\n"
        "| Note | Type | Covers |\n"
        "| --- | --- | --- |"
    ),
    "directives": (
        "## Write boundaries\n"
        "\n"
        "- Never write outside `/nuru/` and `/shared/`\n"
        "\n"
        "## Citation\n"
        "\n"
        "- Cite the note ID any claim came from\n"
        "\n"
        "## Archiving\n"
        "\n"
        "- Ask before archiving anything\n"
        "\n"
        "## Linking\n"
        "\n"
        "- Link by `id`, render the human title when displaying"
    ),
    "daily": "",
    "correction": (
        "## Wrong\n"
        "\n"
        "\n"
        "## Correct\n"
        "\n"
        "\n"
        "## Noticed\n"
        "\n"
        "- {{date}}"
    ),
}
