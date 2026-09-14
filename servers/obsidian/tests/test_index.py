import pytest

from obsidian_mcp import index


def _connect(tmp_path):
    return index.connect(tmp_path / "index.sqlite3")


def test_note_exists_false_for_empty_index(tmp_path):
    conn = _connect(tmp_path)
    assert index.note_exists(conn, "nonexistent") is False


def test_insert_note_then_note_exists_true(tmp_path):
    conn = _connect(tmp_path)
    index.insert_note(
        conn, id="id1", path="shared/x.md", type="project", title="X", author="both",
        created="2026-09-14T12:00", supersedes=None,
    )
    assert index.note_exists(conn, "id1") is True


def test_get_path_returns_none_for_unknown_id(tmp_path):
    conn = _connect(tmp_path)
    assert index.get_path(conn, "nonexistent") is None


def test_get_path_returns_the_stored_path(tmp_path):
    conn = _connect(tmp_path)
    index.insert_note(
        conn, id="id1", path="shared/x.md", type="project", title="X", author="both",
        created="2026-09-14T12:00", supersedes=None,
    )
    assert index.get_path(conn, "id1") == "shared/x.md"


def test_insert_note_rejects_duplicate_id(tmp_path):
    conn = _connect(tmp_path)
    index.insert_note(
        conn, id="id1", path="shared/x.md", type="project", title="X", author="both",
        created="2026-09-14T12:00", supersedes=None,
    )
    with pytest.raises(Exception):
        index.insert_note(
            conn, id="id1", path="shared/y.md", type="project", title="Y", author="both",
            created="2026-09-14T12:01", supersedes=None,
        )


def test_connect_is_idempotent_against_an_existing_database(tmp_path):
    db_path = tmp_path / "index.sqlite3"
    conn1 = index.connect(db_path)
    index.insert_note(
        conn1, id="id1", path="shared/x.md", type="project", title="X", author="both",
        created="2026-09-14T12:00", supersedes=None,
    )
    conn1.close()

    conn2 = index.connect(db_path)
    assert index.note_exists(conn2, "id1") is True
