import pytest
from mcp.server.mcpserver.exceptions import ToolError

from obsidian_mcp import server


# --- list_notes ---


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

    with pytest.raises(ToolError, match="Access denied"):
        server.list_notes()


# --- list_files ---


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


# --- list_files_in_directory ---


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
        with pytest.raises(ToolError, match="Access denied"):
            server.list_files_in_directory("locked")
    finally:
        os.chmod(locked, 0o755)


# --- search_notes ---


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


# --- read_note ---


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


def test_read_note_raises_if_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="FileNotFoundError"):
        server.read_note("NoSuchNote")


def test_read_note_raises_clear_error_on_permission_denied(tmp_path, monkeypatch):
    import os

    note = tmp_path / "Welcome.md"
    note.write_text("Hello World")
    os.chmod(note, 0o000)
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    try:
        with pytest.raises(ToolError, match="Access denied"):
            server.read_note("Welcome.md")
    finally:
        os.chmod(note, 0o644)


# --- list_tags ---


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


# --- count_tags ---


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

    with pytest.raises(ToolError, match="FileNotFoundError"):
        server.read_note("../secret")
    with pytest.raises(ToolError, match="FileNotFoundError"):
        server.read_note("../secret.md")


def test_read_note_rejects_absolute_path_outside_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("TOP SECRET")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    with pytest.raises(ToolError, match="FileNotFoundError"):
        server.read_note(str(outside))


def test_read_note_rejects_nested_dotdot_traversal(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "Sub").mkdir(parents=True)
    (tmp_path / "secret.md").write_text("TOP SECRET")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    with pytest.raises(ToolError, match="FileNotFoundError"):
        server.read_note("Sub/../../secret")


def test_read_note_rejects_symlinked_note_pointing_outside_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("TOP SECRET")
    (vault / "leak.md").symlink_to(outside)
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    with pytest.raises(ToolError, match="FileNotFoundError"):
        server.read_note("leak")
    with pytest.raises(ToolError, match="FileNotFoundError"):
        server.read_note("leak.md")


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
    with pytest.raises(ToolError, match="FileNotFoundError"):
        server.read_note("leak")


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
    with pytest.raises(ToolError, match="FileNotFoundError"):
        server.read_note("leak")


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


# --- create_note ---


def test_create_note_writes_frontmatter_heading_and_body(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.create_note(type="project", title="My Project", author="both", body="Body text.")

    assert result["path"] == "shared/my-project.md"
    text = (tmp_path / "shared" / "my-project.md").read_text()
    assert text.startswith("---\n")
    assert "\n---\n\n# My Project\n\nBody text." in text
    assert f"id: {result['id']}\n" in text


def test_create_note_stamps_the_current_frontmatter_version(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    monkeypatch.setattr(server.template, "VERSION", "9.9.9")

    result = server.create_note(type="project", title="X", author="both")

    assert "version: 9.9.9\n" in (tmp_path / result["path"]).read_text()


def test_create_note_returns_id_and_path(tmp_path, monkeypatch):
    from ulid import ULID

    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.create_note(type="topic", title="Some Topic", author="nuru")
    assert ULID.from_str(result["id"]) is not None
    assert result["path"] == "nuru/some-topic.md"


def test_create_note_maps_type_to_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.create_note(type="directives", title="A", author="user")["path"] == "user/a.md"
    assert server.create_note(type="profile", title="B", author="nuru")["path"] == "nuru/b.md"
    assert server.create_note(type="project", title="C", author="both")["path"] == "shared/c.md"


def test_create_note_slugifies_title_for_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.create_note(type="topic", title="Café & Co: A Story!", author="nuru")
    assert result["path"] == "nuru/caf-co-a-story.md"


def test_create_note_tags_include_type_plus_extras(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.create_note(type="project", title="X", author="both", tags=["extra", "another"])
    text = (tmp_path / result["path"]).read_text()
    assert "tags:\n  - project\n  - extra\n  - another\n" in text


def test_create_note_aliases_are_title_then_id(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.create_note(type="project", title="X", author="both")
    text = (tmp_path / result["path"]).read_text()
    assert f"aliases:\n  - X\n  - {result['id']}\n" in text


def test_create_note_rejects_unknown_type(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="Unknown type"):
        server.create_note(type="bogus", title="X", author="both")


def test_create_note_rejects_unknown_author(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="Unknown author"):
        server.create_note(type="project", title="X", author="bogus")


def test_create_note_rejects_empty_title(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="ValueError"):
        server.create_note(type="project", title="   ", author="both")


def test_create_note_requires_the_type_specific_field(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="ValueError"):
        server.create_note(type="daily", title="X", author="nuru")


def test_create_note_accepts_the_type_specific_field(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.create_note(type="daily", title="X", author="nuru", date="2026-09-14")
    text = (tmp_path / result["path"]).read_text()
    assert "date: '2026-09-14'\n" in text


def test_create_note_rejects_type_specific_field_on_wrong_type(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="ValueError"):
        server.create_note(type="project", title="X", author="both", date="2026-09-14")


def test_create_note_rejects_unknown_supersedes(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="supersedes"):
        server.create_note(type="project", title="X", author="both", supersedes="nonexistent")


def test_create_note_accepts_supersedes_of_an_existing_note(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    original = server.create_note(type="project", title="Original", author="both")
    result = server.create_note(type="project", title="Follow Up", author="both", supersedes=original["id"])
    text = (tmp_path / result["path"]).read_text()
    assert f"supersedes: {original['id']}\n" in text


def test_create_note_refuses_to_overwrite_existing_note(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    server.create_note(type="project", title="X", author="both")

    with pytest.raises(ToolError, match="FileExistsError"):
        server.create_note(type="project", title="X", author="both")


def test_create_note_is_atomic_against_a_racing_duplicate_create(tmp_path, monkeypatch):
    """The existence check and the write must be one atomic filesystem call.

    A check-then-write race would let two concurrent create_note calls for
    the same path both "see" no file and both succeed, one silently
    clobbering the other - exactly the scenario a client retrying after a
    server-side timeout can hit if the original, abandoned write is still
    landing. Simulated here by writing the file in between _resolve_target
    and the actual open("x") call that create_note makes internally.
    """
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    real_resolve_target = server._resolve_target

    def racing_resolve_target(path):
        target = real_resolve_target(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("landed from the other call")
        return target

    monkeypatch.setattr(server, "_resolve_target", racing_resolve_target)

    with pytest.raises(ToolError, match="FileExistsError"):
        server.create_note(type="project", title="X", author="both")
    assert (tmp_path / "shared" / "x.md").read_text() == "landed from the other call"


def test_create_note_rolls_back_the_file_if_the_index_insert_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    def failing_insert_note(*args, **kwargs):
        raise RuntimeError("index insert boom")

    monkeypatch.setattr(server.note_index, "insert_note", failing_insert_note)

    with pytest.raises(RuntimeError, match="boom"):
        server.create_note(type="project", title="X", author="both")
    assert not (tmp_path / "shared" / "x.md").exists()


# --- append_note ---


def test_append_note_appends_to_the_note_s_body(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    created = server.create_note(type="project", title="X", author="both", body="Original.")

    path = server.append_note(created["id"], "More.")

    assert path == created["path"]
    assert (tmp_path / path).read_text().endswith("Original.\n\nMore.")


def test_append_note_inserts_a_blank_line_without_the_caller_adding_one(tmp_path, monkeypatch):
    """Regression test: content used to be concatenated directly onto the end
    of the note's last line with no separator at all (e.g. "First real
    note.Appended line one."), corrupting structure on every append."""
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    created = server.create_note(type="project", title="X", author="both", body="First real note.")

    server.append_note(created["id"], "Appended line one.")

    text = (tmp_path / created["path"]).read_text()
    assert text.endswith("First real note.\n\nAppended line one.")
    assert "note.Appended" not in text


def test_append_note_does_not_add_a_second_blank_line_if_one_already_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    created = server.create_note(type="project", title="X", author="both", body="Original.\n\n")

    server.append_note(created["id"], "More.")

    text = (tmp_path / created["path"]).read_text()
    assert text.endswith("Original.\n\nMore.")
    assert "\n\n\n" not in text


def test_append_note_completes_a_single_trailing_newline_into_a_blank_line(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    created = server.create_note(type="project", title="X", author="both", body="Original.\n")

    server.append_note(created["id"], "More.")

    text = (tmp_path / created["path"]).read_text()
    assert text.endswith("Original.\n\nMore.")


def test_append_note_twice_separates_both_appends(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    created = server.create_note(type="project", title="X", author="both", body="First real note.")

    server.append_note(created["id"], "Appended line one.")
    server.append_note(created["id"], "- second bullet")

    text = (tmp_path / created["path"]).read_text()
    assert text.endswith("First real note.\n\nAppended line one.\n\n- second bullet")


def test_append_note_raises_for_an_unknown_id(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="FileNotFoundError"):
        server.append_note("nonexistent-id", "content")


def test_append_note_does_not_create_a_note_for_a_typo_d_id(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="FileNotFoundError"):
        server.append_note("typo-d-id", "content")
    assert server.list_files() == []


def test_append_note_raises_if_the_indexed_file_was_deleted_outside_the_server(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    created = server.create_note(type="project", title="X", author="both")
    (tmp_path / created["path"]).unlink()

    with pytest.raises(ToolError, match="FileNotFoundError"):
        server.append_note(created["id"], "content")


# --- _run_with_timeout ---


def test_run_with_timeout_returns_the_function_result():
    assert server._run_with_timeout(lambda: 42) == 42


def test_run_with_timeout_propagates_the_function_s_exception():
    def _raise():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        server._run_with_timeout(_raise)


def test_run_with_timeout_raises_timeout_error_on_a_stalled_call(monkeypatch):
    import threading

    monkeypatch.setattr(server, "_IO_TIMEOUT_SECONDS", 0.05)
    release = threading.Event()

    def _stall():
        release.wait(timeout=2)  # simulates a hung filesystem call

    try:
        with pytest.raises(TimeoutError):
            server._run_with_timeout(_stall)
    finally:
        release.set()  # let the abandoned worker thread finish so it doesn't leak


# --- generate_templates ---


def test_generate_templates_writes_one_file_per_type_under_templates(tmp_path, monkeypatch):
    from obsidian_mcp import template_bodies

    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.generate_templates()

    assert result["skipped"] == []
    assert sorted(result["written"]) == sorted(f"Templates/{name}" for name in template_bodies.FILENAMES.values())
    for rel_path in result["written"]:
        assert (tmp_path / rel_path).is_file()


def test_generate_templates_does_not_overwrite_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    (tmp_path / "Templates").mkdir()
    (tmp_path / "Templates" / "01 Profile.md").write_text("stale content")

    result = server.generate_templates()

    assert (tmp_path / "Templates" / "01 Profile.md").read_text() == "stale content"
    assert "Templates/01 Profile.md" in result["skipped"]
    assert "Templates/01 Profile.md" not in result["written"]


def test_generate_templates_overwrites_when_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    (tmp_path / "Templates").mkdir()
    (tmp_path / "Templates" / "01 Profile.md").write_text("stale content")

    result = server.generate_templates(overwrite=True)

    assert (tmp_path / "Templates" / "01 Profile.md").read_text() != "stale content"
    assert "Templates/01 Profile.md" in result["written"]
    assert result["skipped"] == []


def test_generate_templates_leaves_id_blank_and_stamps_a_concrete_author(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    server.generate_templates()

    text = (tmp_path / "Templates" / "01 Profile.md").read_text()
    assert "id:\n" in text
    assert "author: nuru\n" in text


def test_generate_templates_quotes_the_created_placeholder(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    server.generate_templates()

    text = (tmp_path / "Templates" / "01 Profile.md").read_text()
    assert 'created: "{{date}}T{{time}}"\n' in text


def test_generate_templates_daily_uses_date_as_h1_and_has_empty_body(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    server.generate_templates()

    text = (tmp_path / "Templates" / "10 Daily.md").read_text()
    assert text.endswith("# {{date}}\n\n")


def test_generate_templates_other_types_use_title_as_h1(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    server.generate_templates()

    text = (tmp_path / "Templates" / "01 Profile.md").read_text()
    assert "\n\n# {{title}}\n\n## Identity" in text


def test_generate_templates_preserves_trailing_space_on_placeholder_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    server.generate_templates()

    text = (tmp_path / "Templates" / "01 Profile.md").read_text()
    assert "- [stated] Name: \n" in text


def test_generate_templates_ends_with_a_single_trailing_newline(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.generate_templates()

    for rel_path in result["written"]:
        text = (tmp_path / rel_path).read_text()
        assert text.endswith("\n")
        assert not text.endswith("\n\n\n")


def test_generate_templates_correction_targets_field_is_an_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    server.generate_templates()

    text = (tmp_path / "Templates" / "11 Correction.md").read_text()
    assert "targets: []\n" in text


# --- rename_or_move_file ---


def test_rename_or_move_file_renames_within_same_directory(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("content")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.rename_or_move_file("Welcome.md", "Hello.md") == "Hello.md"
    assert not (tmp_path / "Welcome.md").exists()
    assert (tmp_path / "Hello.md").read_text() == "content"


def test_rename_or_move_file_moves_to_new_directory(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("content")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.rename_or_move_file("Welcome.md", "Projects/Welcome.md") == "Projects/Welcome.md"
    assert (tmp_path / "Projects" / "Welcome.md").read_text() == "content"


def test_rename_or_move_file_raises_if_source_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="FileNotFoundError"):
        server.rename_or_move_file("NoSuchNote.md", "Elsewhere.md")


def test_rename_or_move_file_refuses_to_overwrite_existing_destination(tmp_path, monkeypatch):
    (tmp_path / "Source.md").write_text("source content")
    (tmp_path / "Dest.md").write_text("dest content")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="FileExistsError"):
        server.rename_or_move_file("Source.md", "Dest.md")
    assert (tmp_path / "Dest.md").read_text() == "dest content"


def test_rename_or_move_file_rejects_dotdot_traversal_outside_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Welcome.md").write_text("content")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    with pytest.raises(ToolError, match="ValueError"):
        server.rename_or_move_file("Welcome.md", "../escaped.md")
    assert (vault / "Welcome.md").exists()
    assert not (tmp_path / "escaped.md").exists()


def test_rename_or_move_file_rejects_symlinked_source_pointing_outside_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("TOP SECRET")
    (vault / "leak.md").symlink_to(outside)
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    with pytest.raises(ToolError, match="ValueError"):
        server.rename_or_move_file("leak.md", "moved.md")
    assert outside.exists()


def test_rename_or_move_file_rejects_source_inside_dot_obsidian(tmp_path, monkeypatch):
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text("{}")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="PermissionError"):
        server.rename_or_move_file(".obsidian/app.json", "app.json")
    assert (tmp_path / ".obsidian" / "app.json").exists()


def test_rename_or_move_file_rejects_destination_inside_dot_obsidian(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("content")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="PermissionError"):
        server.rename_or_move_file("Welcome.md", ".obsidian/hidden.md")


def test_rename_or_move_file_rejects_source_under_user_folder(tmp_path, monkeypatch):
    (tmp_path / "user").mkdir()
    (tmp_path / "user" / "note.md").write_text("content")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="PermissionError"):
        server.rename_or_move_file("user/note.md", "note.md")
    assert (tmp_path / "user" / "note.md").exists()


def test_rename_or_move_file_rejects_destination_under_user_folder(tmp_path, monkeypatch):
    (tmp_path / "note.md").write_text("content")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="PermissionError"):
        server.rename_or_move_file("note.md", "user/note.md")


# --- delete_file_or_directory ---


def test_delete_file_or_directory_deletes_a_file(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("content")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.delete_file_or_directory("Welcome.md") == ["Welcome.md"]
    assert not (tmp_path / "Welcome.md").exists()


def test_delete_file_or_directory_deletes_a_directory_recursively(tmp_path, monkeypatch):
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "Nuru MCP.md").write_text("content")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    manifest = server.delete_file_or_directory("Projects")

    assert sorted(manifest) == ["Projects", "Projects/Nuru MCP.md"]
    assert not (tmp_path / "Projects").exists()


def test_delete_file_or_directory_raises_if_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="FileNotFoundError"):
        server.delete_file_or_directory("NoSuchNote.md")


def test_delete_file_or_directory_refuses_to_delete_vault_root(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("content")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="ValueError"):
        server.delete_file_or_directory(".")
    assert (tmp_path / "Welcome.md").exists()


def test_delete_file_or_directory_rejects_dotdot_traversal_outside_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("TOP SECRET")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    with pytest.raises(ToolError, match="ValueError"):
        server.delete_file_or_directory("../outside.md")
    assert outside.exists()


def test_delete_file_or_directory_rejects_dot_obsidian_contents(tmp_path, monkeypatch):
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text("{}")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="PermissionError"):
        server.delete_file_or_directory(".obsidian/app.json")
    assert (tmp_path / ".obsidian" / "app.json").exists()


def test_delete_file_or_directory_rejects_dot_obsidian_itself(tmp_path, monkeypatch):
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text("{}")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="PermissionError"):
        server.delete_file_or_directory(".obsidian")
    assert (tmp_path / ".obsidian").exists()


def test_delete_file_or_directory_rejects_the_index_db(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    server.create_note(type="project", title="X", author="both")  # creates the index db

    with pytest.raises(ToolError, match="PermissionError"):
        server.delete_file_or_directory(server._INDEX_DB_NAME)
    assert (tmp_path / server._INDEX_DB_NAME).exists()


def test_delete_file_or_directory_rejects_a_path_under_user_folder(tmp_path, monkeypatch):
    (tmp_path / "user").mkdir()
    (tmp_path / "user" / "note.md").write_text("content")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="PermissionError"):
        server.delete_file_or_directory("user/note.md")
    assert (tmp_path / "user" / "note.md").exists()


def test_delete_file_or_directory_rejects_symlinked_target_pointing_outside_vault(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("TOP SECRET")
    (vault / "leak.md").symlink_to(outside)
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    with pytest.raises(ToolError, match="ValueError"):
        server.delete_file_or_directory("leak.md")
    assert outside.exists()
