import pytest

from obsidian_mcp import server


def test_list_notes_empty_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.list_notes() == []


def test_list_notes_returns_markdown_files_only(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("# Welcome")
    (tmp_path / "notes.txt").write_text("not markdown")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.list_notes() == ["Welcome.md"]


def test_list_notes_includes_nested_files_as_relative_paths(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("# Welcome")
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "Nuru MCP.md").write_text("# Nuru MCP")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.list_notes() == ["Projects/Nuru MCP.md", "Welcome.md"]


def test_list_notes_raises_clear_error_on_permission_denied(monkeypatch):
    class DeniedPath:
        def iterdir(self):
            raise PermissionError("denied")

    monkeypatch.setattr(server, "VAULT_PATH", DeniedPath())

    with pytest.raises(PermissionError, match="Access denied"):
        server.list_notes()


def test_list_files_includes_notes_and_attachments(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("# Welcome")
    (tmp_path / "diagram.png").write_bytes(b"fake image data")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.list_files() == ["Welcome.md", "diagram.png"]


def test_list_files_excludes_obsidian_config_and_dotfiles(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("# Welcome")
    (tmp_path / ".DS_Store").write_text("junk")
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text("{}")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.list_files() == ["Welcome.md"]


def test_list_files_in_directory_lists_recursively(tmp_path, monkeypatch):
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "Nuru MCP.md").write_text("# Nuru MCP")
    (tmp_path / "Projects" / "Sub").mkdir()
    (tmp_path / "Projects" / "Sub" / "diagram.png").write_bytes(b"data")
    (tmp_path / "Other.md").write_text("# Other")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.list_files_in_directory("Projects") == [
        "Projects/Nuru MCP.md",
        "Projects/Sub/diagram.png",
    ]


def test_list_files_in_directory_returns_empty_list_if_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.list_files_in_directory("NoSuchDir") == []


def test_list_files_in_directory_finds_first_nested_match_by_name(tmp_path, monkeypatch):
    (tmp_path / "Projects" / "folder").mkdir(parents=True)
    (tmp_path / "Projects" / "folder" / "a.md").write_text("a")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.list_files_in_directory("folder") == ["Projects/folder/a.md"]


def test_list_files_in_directory_raises_clear_error_on_permission_denied(tmp_path, monkeypatch):
    import os

    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o000)
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    try:
        with pytest.raises(PermissionError, match="Access denied"):
            server.list_files_in_directory("locked")
    finally:
        os.chmod(locked, 0o755)


def test_search_notes_finds_matching_notes_case_insensitively(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("Hello World")
    (tmp_path / "Other.md").write_text("Nothing relevant here")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.search_notes("hello") == ["Welcome.md"]

def test_search_notes_finds_partial_matching_notes_case_insensitively(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("Hello World")
    (tmp_path / "Other.md").write_text("Nothing relevant here")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.search_notes("ell") == ["Welcome.md"]

def test_search_notes_finds_tags_in_notes_case_insensitively(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("Hello World")
    (tmp_path / "Other.md").write_text("Nothing #relevant here")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.search_notes("#relevant") == ["Other.md"]

def test_search_notes_returns_empty_list_when_not_found(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("Hello World")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.search_notes("nonexistent") == []


def test_read_note_with_extension(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("Hello World")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.read_note("Welcome.md") == "Hello World"


def test_read_note_without_extension(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("Hello World")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.read_note("Welcome") == "Hello World"


def test_read_note_finds_nested_note_by_name(tmp_path, monkeypatch):
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "Nuru MCP.md").write_text("# Nuru MCP")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.read_note("Nuru MCP") == "# Nuru MCP"


def test_read_note_with_relative_subdirectory_path(tmp_path, monkeypatch):
    (tmp_path / "Learning").mkdir()
    (tmp_path / "Learning" / "Agents.md").write_text("# Agents note content")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.read_note("Learning/Agents.md") == "# Agents note content"


def test_read_note_returns_empty_string_if_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.read_note("NoSuchNote") == ""


def test_read_note_raises_clear_error_on_permission_denied(tmp_path, monkeypatch):
    import os

    note = tmp_path / "Welcome.md"
    note.write_text("Hello World")
    os.chmod(note, 0o000)
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    try:
        with pytest.raises(PermissionError, match="Access denied"):
            server.read_note("Welcome.md")
    finally:
        os.chmod(note, 0o644)
