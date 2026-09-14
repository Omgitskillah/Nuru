"""The frontmatter template create_note writes: what fields exist, and their
allowed values. Kept separate from server.py so the shape of a note's
frontmatter is one importable, reviewable thing rather than scattered through
the tool's logic.

Notes are append-only: this template is only ever applied at creation, never
to rewrite an existing note. VERSION is stamped into every note create_note
writes, recording which version of this template it was created under.

Bump VERSION (semver) whenever this file's shape changes - a field added,
renamed, or reordered in server.py's frontmatter construction, or a change to
TYPE_FOLDER/VALID_AUTHORS/TYPE_FIELD below. Never bump it retroactively: an
existing note's stamped version must keep describing the template that was
actually current when that note was written, so a future reader can branch on
`version` to handle older structures correctly instead of assuming today's
shape applies everywhere.
"""

VERSION = "0.1.0"

# The authoritative definition of what each VERSION means: the exact field
# list, order, and semantics that version's frontmatter has. A note's
# `version` field is this table's key - anything reading notes back (rather
# than only ever writing new ones, which is all this server does today)
# looks its note's version up here to know how to parse it, rather than
# assuming the current shape applies to every note in the vault.
#
# 0.1.0 (current):
#   Field order: id, version, type, created, author, supersedes,
#   [<type-specific field>], tags, aliases.
#     - id: ULID, minted once by create_note. Immutable - append_note never
#       touches frontmatter.
#     - version: this table's key.
#     - type: one of TYPE_FOLDER's keys; fixes the note's folder.
#     - created: "YYYY-MM-DDTHH:mm", server clock, set once at creation.
#     - author: one of VALID_AUTHORS.
#     - supersedes: id of the note this one replaces, or blank.
#     - <type-specific field>: present only for a type in TYPE_FIELD, named
#       and validated per TYPE_FIELD (date: "YYYY-MM-DD"; session:
#       "YYYY-MM-DD-NN"; targets: a list of existing note ids).
#     - tags: block list, [type, *any extra caller-supplied tags]. No tag
#       contains whitespace.
#     - aliases: block list, [title, id].



# Frontmatter fields the server owns are minted here, never accepted from the
# caller. Folder is keyed by type; the eleven types are exactly these keys.
TYPE_FOLDER = {
    "directives": "user",
    "daily": "user",
    "correction": "user",
    "profile": "nuru",
    "preferences": "nuru",
    "person": "nuru",
    "topic": "nuru",
    "index": "nuru",
    "transcript": "nuru",
    "project": "shared",
    "session": "shared",
}

VALID_AUTHORS = {"user", "nuru", "both"}

# Extra frontmatter field each type requires, beyond the universal ones.
# type_fields passed to create_note must match exactly - no more, no less.
TYPE_FIELD = {
    "daily": "date",
    "correction": "targets",
    "transcript": "session",
    "session": "session",
}

# Default `author` generate_templates stamps into each type's template file.
# create_note itself requires the caller to state an author explicitly for a
# real note; this is only for the Templates/ skeletons, which need *some*
# concrete value to be valid frontmatter (there's no {{author}} token).
TEMPLATE_AUTHOR = {
    "directives": "user",
    "daily": "user",
    "correction": "user",
    "profile": "nuru",
    "preferences": "nuru",
    "person": "nuru",
    "topic": "nuru",
    "index": "nuru",
    "transcript": "nuru",
    "project": "both",
    "session": "nuru",
}
