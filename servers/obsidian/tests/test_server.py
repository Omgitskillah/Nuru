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


def test_list_tags_returns_unique_sorted_tags(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("#test #dummy-vault")
    (tmp_path / "Other.md").write_text("Nothing #relevant here, also #test again")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.list_tags() == ["dummy-vault", "relevant", "test"]


def test_list_tags_ignores_markdown_headings(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("# Welcome\n\nNo tags here.")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.list_tags() == []


def test_list_tags_empty_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.list_tags() == []


def test_count_tags_counts_occurrences_across_notes(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("#test #dummy-vault")
    (tmp_path / "Other.md").write_text("Nothing #relevant here, also #test again")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.count_tags() == {"dummy-vault": 1, "relevant": 1, "test": 2}


def test_count_tags_empty_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.count_tags() == {}


# --- Vault containment: read_note ---


def test_read_note_rejects_dotdot_traversal_outside_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (tmp_path / "secret.md").write_text("TOP SECRET")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    assert server.read_note("../secret") == ""
    assert server.read_note("../secret.md") == ""


def test_read_note_rejects_absolute_path_outside_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("TOP SECRET")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    assert server.read_note(str(outside)) == ""


def test_read_note_rejects_nested_dotdot_traversal(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "Sub").mkdir(parents=True)
    (tmp_path / "secret.md").write_text("TOP SECRET")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    assert server.read_note("Sub/../../secret") == ""


def test_read_note_rejects_symlinked_note_pointing_outside_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("TOP SECRET")
    (vault / "leak.md").symlink_to(outside)
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    assert server.read_note("leak") == ""
    assert server.read_note("leak.md") == ""


def test_read_note_rejects_symlinked_note_found_via_nested_fallback_search(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    (vault / "Sub").mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("TOP SECRET")
    (vault / "Sub" / "leak.md").symlink_to(outside)
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    # Looked up by bare name, so it hits the recursive fallback search.
    assert server.read_note("leak") == ""


def test_read_note_still_reads_legitimate_note_alongside_a_bad_symlink(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("TOP SECRET")
    (vault / "leak.md").symlink_to(outside)
    (vault / "Welcome.md").write_text("Hello World")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    assert server.read_note("Welcome") == "Hello World"
    assert server.read_note("leak") == ""


def test_read_note_allows_symlink_pointing_within_the_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Real.md").write_text("Real content")
    (vault / "Alias.md").symlink_to(vault / "Real.md")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    assert server.read_note("Alias") == "Real content"


# --- Vault containment: list_files_in_directory ---


def test_list_files_in_directory_rejects_dotdot_traversal_outside_vault(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.md").write_text("TOP SECRET")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    assert server.list_files_in_directory("../outside") == []
    assert server.list_files_in_directory("..") == []


def test_list_files_in_directory_rejects_absolute_path_outside_vault(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.md").write_text("TOP SECRET")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    assert server.list_files_in_directory(str(outside_dir)) == []


def test_list_files_in_directory_rejects_nested_dotdot_traversal(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "Projects").mkdir(parents=True)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.md").write_text("TOP SECRET")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    assert server.list_files_in_directory("Projects/../../outside") == []


def test_list_files_in_directory_rejects_symlinked_directory_pointing_outside(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.md").write_text("TOP SECRET")
    (vault / "link").symlink_to(outside_dir)
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    assert server.list_files_in_directory("link") == []


def test_list_files_in_directory_rejects_symlinked_directory_found_via_fallback(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    (vault / "Projects").mkdir(parents=True)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.md").write_text("TOP SECRET")
    (vault / "Projects" / "link").symlink_to(outside_dir)
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    # Looked up by bare name, so it hits the recursive fallback search.
    assert server.list_files_in_directory("link") == []


def test_list_files_in_directory_excludes_symlinked_subdir_pointing_outside(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    (vault / "Projects").mkdir(parents=True)
    (vault / "Projects" / "real.md").write_text("real content")
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.md").write_text("TOP SECRET")
    (vault / "Projects" / "link").symlink_to(outside_dir)
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    assert server.list_files_in_directory("Projects") == ["Projects/real.md"]


def test_list_files_in_directory_allows_symlinked_directory_within_the_vault(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    (vault / "Real").mkdir(parents=True)
    (vault / "Real" / "note.md").write_text("content")
    (vault / "Alias").symlink_to(vault / "Real")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    # The resolved target is used, so paths come back under its real name.
    assert server.list_files_in_directory("Alias") == ["Real/note.md"]
