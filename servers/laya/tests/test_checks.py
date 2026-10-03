import pytest
from ulid import ULID

from laya_mcp.checks import check_write, find_note_ids, invented_ids

KNOWN = str(ULID())
UNKNOWN = str(ULID())


def test_a_tool_off_the_write_allowlist_is_blocked():
    assert "not a write tool" in check_write("read_note", {"name": "x"}, set())[0]


@pytest.mark.parametrize("tool, arguments", [
    ("rename_or_move_file", {"source_path": "user/daily-2026-09-17.md", "destination_path": "nuru/x.md"}),
    ("rename_or_move_file", {"source_path": "nuru/x.md", "destination_path": "user/x.md"}),
    ("rename_or_move_file", {"source_path": "user/directives-for-local-model.md", "destination_path": "trash"}),
])
def test_structural_tools_stay_out_of_the_users_folder(tool, arguments):
    assert any("under user/" in r for r in check_write(tool, arguments, set()))


def test_a_users_note_type_needs_the_user_as_author():
    reasons = check_write("write_note", {"note": "x", "type": "directives", "author": "user", "content": "c"}, set())
    assert reasons == ["a directives note lives in user/, the user's own folder, which Nuru never writes"]
    assert "the user's own folder" in check_write("write_note", {"note": "user/plan.md", "content": "c"}, set())[0]


def test_todays_daily_needs_no_author():
    assert check_write("write_note", {"type": "daily", "content": "c"}, set()) == []


def test_nuru_may_file_a_correction_pointing_at_known_notes():
    args = {"note": "x", "type": "correction", "author": "nuru", "targets": [KNOWN], "content": "c"}
    assert check_write("write_note", args, {KNOWN}) == []


def test_an_unknown_note_type_is_blocked():
    reasons = check_write("write_note", {"note": "x", "type": "memo", "author": "nuru", "content": "c"}, set())
    assert "not one of" in reasons[0]


def test_an_archived_note_is_not_written_and_trash_is_one_way():
    assert "is archived" in check_write("write_note", {"note": "archive/nuru/x.md", "content": "c"}, set())[0]
    reasons = check_write("rename_or_move_file", {"source_path": "trash/nuru/x.md", "destination_path": "nuru/x.md"}, set())
    assert reasons == ["'trash/nuru/x.md' is in trash/, and nothing comes back out of trash"]
    assert check_write("rename_or_move_file", {"source_path": "nuru/x.md", "destination_path": "trash"}, set()) == []


@pytest.mark.parametrize("path, problem", [
    ("../outside.md", "outside the vault"),
    ("/etc/passwd", "outside the vault"),
    (".obsidian/app.json", "hidden/config folder"),
    ("Templates/01 Profile.md", "Templates/"),
])
def test_path_rules(path, problem):
    reasons = check_write("rename_or_move_file", {"source_path": "nuru/x.md", "destination_path": path}, set())
    assert any(problem in r for r in reasons)


def test_a_note_named_by_id_needs_an_id_a_search_returned():
    assert check_write("write_note", {"note": KNOWN, "content": "x"}, {KNOWN}) == []
    assert "not returned by any search" in check_write("write_note", {"note": UNKNOWN, "content": "x"}, {KNOWN})[0]
    assert check_write("write_note", {"note": "Priya Raman", "content": "x"}, {KNOWN}) == []


def test_supersedes_and_targets_need_known_ids_too():
    reasons = check_write(
        "write_note",
        {"note": "x", "type": "topic", "author": "nuru", "supersedes": UNKNOWN, "targets": None, "content": "c"},
        {KNOWN},
    )
    assert reasons == [f"supersedes {UNKNOWN} was not returned by any search this session"]


def test_note_ids_are_found_in_prose_and_unknown_ones_reported():
    reply = f"I appended to {KNOWN} and also updated {UNKNOWN}."
    assert find_note_ids(reply) == {KNOWN, UNKNOWN}
    assert invented_ids(reply, {KNOWN}) == [UNKNOWN]


def test_ordinary_words_are_not_mistaken_for_ids():
    assert find_note_ids("ABCDEFGHJKMNPQRSTVWXYZ is 22 chars; 2026-09-28T10:00 is a time.") == set()


def test_only_notes_in_nuru_can_be_replaced():
    assert check_write("write_note", {"note": "nuru/x.md", "content": "c", "mode": "replace"}, set()) == []
    assert "only Nuru's own notes can be replaced" in check_write(
        "write_note", {"note": "shared/plan.md", "content": "c", "mode": "replace"}, set())[0]
