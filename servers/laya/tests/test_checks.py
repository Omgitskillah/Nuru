import pytest
from ulid import ULID

from laya_mcp.checks import check_write, find_note_ids, invented_ids

KNOWN = str(ULID())
UNKNOWN = str(ULID())


def test_delete_is_blocked_whatever_the_arguments():
    assert check_write("delete_file_or_directory", {"path": "nuru/x.md"}, set()) == [
        "delete_file_or_directory is blocked; archive the note instead"
    ]


def test_a_tool_off_the_write_allowlist_is_blocked():
    assert "not a write tool" in check_write("read_note", {"name": "x"}, set())[0]


@pytest.mark.parametrize("tool, arguments", [
    ("rename_or_move_file", {"source_path": "user/daily-2026-09-17.md", "destination_path": "nuru/x.md"}),
    ("rename_or_move_file", {"source_path": "nuru/x.md", "destination_path": "user/x.md"}),
    ("archive_note", {"name": "user/directives-for-local-model.md"}),
])
def test_structural_tools_stay_out_of_the_users_folder(tool, arguments):
    assert any("under user/" in r for r in check_write(tool, arguments, set()))


def test_a_users_note_type_needs_the_user_as_author():
    reasons = check_write("create_note", {"type": "directives", "title": "x", "author": "nuru"}, set())
    assert reasons == ["a directives note is the user's own and needs author 'user'"]
    assert check_write("create_note", {"type": "directives", "title": "x", "author": "user"}, set()) == []


def test_nuru_may_file_a_correction_pointing_at_known_notes():
    args = {"type": "correction", "title": "x", "author": "nuru", "targets": [KNOWN]}
    assert check_write("create_note", args, {KNOWN}) == []


def test_an_unknown_note_type_is_blocked():
    assert "not one of" in check_write("create_note", {"type": "memo", "title": "x", "author": "nuru"}, set())[0]


@pytest.mark.parametrize("path, problem", [
    ("../outside.md", "outside the vault"),
    ("/etc/passwd", "outside the vault"),
    (".obsidian/app.json", "hidden/config folder"),
    ("Templates/01 Profile.md", "Templates/"),
])
def test_path_rules(path, problem):
    reasons = check_write("rename_or_move_file", {"source_path": "nuru/x.md", "destination_path": path}, set())
    assert any(problem in r for r in reasons)


def test_generate_templates_may_not_overwrite():
    assert check_write("generate_templates", {}, set()) == []
    assert "overwrite" in check_write("generate_templates", {"overwrite": True}, set())[0]


def test_append_needs_an_id_a_search_returned():
    assert check_write("append_note", {"id": KNOWN, "content": "x"}, {KNOWN}) == []
    assert "not returned by any search" in check_write("append_note", {"id": UNKNOWN, "content": "x"}, {KNOWN})[0]
    assert "is not a note ID" in check_write("append_note", {"id": "priya-raman", "content": "x"}, {KNOWN})[0]


def test_supersedes_and_targets_need_known_ids_too():
    reasons = check_write(
        "create_note",
        {"type": "topic", "title": "x", "author": "nuru", "supersedes": UNKNOWN, "targets": None},
        {KNOWN},
    )
    assert reasons == [f"supersedes {UNKNOWN} was not returned by any search this session"]


def test_note_ids_are_found_in_prose_and_unknown_ones_reported():
    reply = f"I appended to {KNOWN} and also updated {UNKNOWN}."
    assert find_note_ids(reply) == {KNOWN, UNKNOWN}
    assert invented_ids(reply, {KNOWN}) == [UNKNOWN]


def test_ordinary_words_are_not_mistaken_for_ids():
    assert find_note_ids("ABCDEFGHJKMNPQRSTVWXYZ is 22 chars; 2026-09-28T10:00 is a time.") == set()
