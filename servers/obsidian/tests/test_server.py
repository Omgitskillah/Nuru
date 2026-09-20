import sqlite3

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


def test_list_notes_excludes_templates_folder(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("# Welcome")
    (tmp_path / "Templates").mkdir()
    (tmp_path / "Templates" / "01 Profile.md").write_text("---\n---\n\n# {{title}}\n")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.list_notes() == ["Welcome.md"]


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

    result = server.list_files_in_directory("Projects")

    assert result["files"] == ["Projects/Nuru MCP.md", "Projects/Sub/diagram.png"]
    assert result["count"] == 2
    assert result["directory"] == "Projects"
    assert "hint" not in result


def test_list_files_in_directory_returns_empty_files_and_a_hint_if_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.list_files_in_directory("NoSuchDir")

    assert result["files"] == []
    assert result["count"] == 0
    assert "NoSuchDir" in result["hint"]
    assert "list_files" in result["hint"]


def test_list_files_in_directory_hints_when_the_directory_exists_but_is_empty(tmp_path, monkeypatch):
    (tmp_path / "Empty").mkdir()
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.list_files_in_directory("Empty")

    assert result["files"] == []
    assert "exists but contains no visible files" in result["hint"]


def test_list_files_in_directory_finds_first_nested_match_by_name(tmp_path, monkeypatch):
    (tmp_path / "Projects" / "folder").mkdir(parents=True)
    (tmp_path / "Projects" / "folder" / "a.md").write_text("a")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.list_files_in_directory("folder")["files"] == ["Projects/folder/a.md"]


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


def _search_paths(result):
    return [match["path"] for match in result["matches"]]


def test_search_notes_finds_matching_notes_case_insensitively(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("Hello World")
    (tmp_path / "Other.md").write_text("Nothing relevant here")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.search_notes("hello")

    assert _search_paths(result) == ["Welcome.md"]
    assert result["count"] == 1
    assert result["query"] == "hello"
    assert "hint" not in result

def test_search_notes_finds_partial_matching_notes_case_insensitively(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("Hello World")
    (tmp_path / "Other.md").write_text("Nothing relevant here")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert _search_paths(server.search_notes("ell")) == ["Welcome.md"]

def test_search_notes_finds_tags_in_notes_case_insensitively(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("Hello World")
    (tmp_path / "Other.md").write_text("Nothing #relevant here")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert _search_paths(server.search_notes("#relevant")) == ["Other.md"]

def test_search_notes_returns_no_matches_and_a_retry_hint_when_not_found(tmp_path, monkeypatch):
    (tmp_path / "Welcome.md").write_text("Hello World")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.search_notes("nonexistent")

    assert result["matches"] == []
    assert result["count"] == 0
    assert "nonexistent" in result["hint"]
    assert "fewer or different words" in result["hint"]


def test_search_notes_requires_every_word_in_any_order(tmp_path, monkeypatch):
    (tmp_path / "Outage.md").write_text("The modem dropped the connection at noon")
    (tmp_path / "Modem.md").write_text("The modem is fine")
    (tmp_path / "Link.md").write_text("Only the connection is mentioned")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    # Word order in the query is the reverse of the text's.
    assert _search_paths(server.search_notes("connection modem")) == ["Outage.md"]


def test_search_notes_treats_hyphen_underscore_and_space_alike(tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("Saw a PDP-drop at noon")
    (tmp_path / "b.md").write_text("PDP_drop happened again")
    (tmp_path / "c.md").write_text("PDP only")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    for query in ("PDP drop", "PDP-drop", "pdp_drop"):
        assert _search_paths(server.search_notes(query)) == ["a.md", "b.md"], query


def test_search_notes_matches_a_hyphenated_word_as_an_adjacent_pair(tmp_path, monkeypatch):
    """Dates are searched as e.g. "09-17": that must not match every note that
    merely mentions 09 and 17 somewhere (a time and a count, say)."""
    (tmp_path / "a.md").write_text("Standup on 2026-09-17")
    (tmp_path / "b.md").write_text("Call at 09:00, lead time 17 weeks")
    (tmp_path / "c.md").write_text("Moved to 09 17 in the plan")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert _search_paths(server.search_notes("09-17")) == ["a.md", "c.md"]


def test_search_notes_searches_the_filename_not_just_the_body(tmp_path, monkeypatch):
    (tmp_path / "devops").mkdir()
    (tmp_path / "devops" / "Kubernetes.md").write_text("Cluster setup notes.")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.search_notes("kubernetes")

    assert _search_paths(result) == ["devops/Kubernetes.md"]
    # Nothing in the body matched, so the snippet is just the start of the body.
    assert result["matches"][0]["snippet"] == "Cluster setup notes."


def test_search_notes_ranks_filename_and_title_hits_above_body_hits(tmp_path, monkeypatch):
    # Alphabetically "Alpha" and "Beta" come first, so only ranking puts Kubernetes.md on top.
    (tmp_path / "Alpha.md").write_text("Mentions kubernetes in passing")
    (tmp_path / "Beta.md").write_text("Also mentions kubernetes")
    (tmp_path / "Kubernetes.md").write_text("Cluster setup notes.")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    created = server.create_note(type="topic", title="Kubernetes Upgrade", author="nuru", body="Steps.")

    paths = _search_paths(server.search_notes("kubernetes"))

    assert set(paths[:2]) == {"Kubernetes.md", created["path"]}
    assert paths[2:] == ["Alpha.md", "Beta.md"]


def test_search_notes_returns_id_type_title_and_snippet_for_indexed_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    created = server.create_note(type="topic", title="Battery Life", author="nuru", body="Discussing the supercap.")

    match = server.search_notes("supercap")["matches"][0]

    assert match["path"] == created["path"]
    assert match["id"] == created["id"]
    assert match["type"] == "topic"
    assert match["title"] == "Battery Life"
    assert "supercap" in match["snippet"]


def test_search_notes_returns_null_id_and_type_for_notes_not_made_by_create_note(tmp_path, monkeypatch):
    (tmp_path / "Plain.md").write_text("Some text about routers")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    match = server.search_notes("routers")["matches"][0]

    assert match == {
        "path": "Plain.md",
        "id": None,
        "type": None,
        "title": "Plain",
        "snippet": "Some text about routers",
    }


def test_search_notes_does_not_create_the_index_file(tmp_path, monkeypatch):
    (tmp_path / "Plain.md").write_text("Some text")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    server.search_notes("text")

    assert not (tmp_path / server._INDEX_DB_NAME).exists()


def test_search_notes_snippet_is_a_window_around_the_first_hit_and_skips_frontmatter(tmp_path, monkeypatch):
    body = "x" * 300 + "\n\nthe router firmware\nis old " + "y" * 300
    (tmp_path / "Long.md").write_text(f"---\ntags:\n  - router\n---\n\n# Long\n\n{body}")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    snippet = server.search_notes("router")["matches"][0]["snippet"]

    assert "router firmware is old" in snippet  # newlines collapsed to spaces
    assert "tags:" not in snippet
    assert snippet.startswith("…") and snippet.endswith("…")
    assert len(snippet) <= server._SNIPPET_CHARS + 2


def test_search_notes_caps_results_and_reports_the_true_count(tmp_path, monkeypatch):
    for i in range(12):
        (tmp_path / f"note{i:02}.md").write_text("A widget note")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.search_notes("widget")

    assert len(result["matches"]) == 10
    assert result["count"] == 12
    assert "10 of 12" in result["hint"]


@pytest.mark.parametrize("query", ["", "   ", "-_ -"])
def test_search_notes_rejects_a_query_with_no_words(tmp_path, monkeypatch, query):
    (tmp_path / "Welcome.md").write_text("Hello World")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="empty"):
        server.search_notes(query)


def test_search_notes_excludes_templates_folder(tmp_path, monkeypatch):
    """Regression test: search_notes("{{title}}") used to return all eleven
    generated templates, so an agent could retrieve unfilled placeholder text
    as if it were real vault content."""
    (tmp_path / "Templates").mkdir()
    (tmp_path / "Templates" / "01 Profile.md").write_text("---\n---\n\n# {{title}}\n")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    assert server.search_notes("{{title}}")["matches"] == []


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


def test_list_tags_excludes_templates_folder(tmp_path, monkeypatch):
    (tmp_path / "Templates").mkdir()
    (tmp_path / "Templates" / "01 Profile.md").write_text("#profile")
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


def test_count_tags_excludes_templates_folder(tmp_path, monkeypatch):
    (tmp_path / "Templates").mkdir()
    (tmp_path / "Templates" / "01 Profile.md").write_text("#profile")
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

    assert server.list_files_in_directory("../outside")["files"] == []
    assert server.list_files_in_directory("..")["files"] == []


def test_list_files_in_directory_rejects_absolute_path_outside_vault(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.md").write_text("TOP SECRET")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    assert server.list_files_in_directory(str(outside_dir))["files"] == []


def test_list_files_in_directory_rejects_nested_dotdot_traversal(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "Projects").mkdir(parents=True)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.md").write_text("TOP SECRET")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    assert server.list_files_in_directory("Projects/../../outside")["files"] == []


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

    assert server.list_files_in_directory("link")["files"] == []


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
    assert server.list_files_in_directory("link")["files"] == []


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

    assert server.list_files_in_directory("Projects")["files"] == ["Projects/real.md"]


def test_list_files_in_directory_allows_symlinked_directory_within_the_vault(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    (vault / "Real").mkdir(parents=True)
    (vault / "Real" / "note.md").write_text("content")
    (vault / "Alias").symlink_to(vault / "Real")
    monkeypatch.setattr(server, "VAULT_PATH", vault)

    # The resolved target is used, so paths come back under its real name.
    assert server.list_files_in_directory("Alias")["files"] == ["Real/note.md"]


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
    assert result["path"] == "nuru/cafe-co-a-story.md"


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

    with pytest.raises(ToolError, match="expects exactly these extra fields"):
        server.create_note(type="daily", title="X", author="user")


def test_create_note_accepts_the_type_specific_field(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.create_note(type="daily", title="X", author="user", date="2026-09-14")
    text = (tmp_path / result["path"]).read_text()
    assert "date: '2026-09-14'\n" in text


def test_create_note_rejects_type_specific_field_on_wrong_type(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="ValueError"):
        server.create_note(type="project", title="X", author="both", date="2026-09-14")


def test_create_note_rejects_a_malformed_date(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="date must be 'YYYY-MM-DD'"):
        server.create_note(type="daily", title="X", author="user", date="not-a-date")


def test_create_note_rejects_a_date_that_is_not_a_real_calendar_date(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="not a valid calendar date"):
        server.create_note(type="daily", title="X", author="user", date="2026-02-30")


def test_create_note_rejects_a_malformed_session(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="session must be 'YYYY-MM-DD-NN'"):
        server.create_note(type="transcript", title="X", author="nuru", session="garbage-session-value")


def test_create_note_accepts_a_well_formed_session(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.create_note(type="transcript", title="X", author="nuru", session="2026-09-14-01")
    text = (tmp_path / result["path"]).read_text()
    assert "session: 2026-09-14-01\n" in text


def test_create_note_rejects_targets_referencing_unknown_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="unknown note id"):
        server.create_note(type="correction", title="X", author="user", targets=["nope1", "nope2"])


def test_create_note_accepts_targets_referencing_existing_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    original = server.create_note(type="project", title="Original", author="both")

    result = server.create_note(type="correction", title="Fix", author="user", targets=[original["id"]])
    text = (tmp_path / result["path"]).read_text()
    assert f"targets:\n  - {original['id']}\n" in text


def test_create_note_rejects_a_tag_containing_whitespace(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="whitespace"):
        server.create_note(type="project", title="X", author="both", tags=["bad\n\n author: hacked"])


def test_create_note_accepts_tags_without_whitespace(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.create_note(type="project", title="X", author="both", tags=["good-tag"])
    text = (tmp_path / result["path"]).read_text()
    assert "  - good-tag\n" in text


def test_create_note_transliterates_accented_latin_characters(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.create_note(type="topic", title="Café Story", author="nuru")
    assert result["path"] == "nuru/cafe-story.md"


def test_create_note_transliterates_a_non_latin_title_instead_of_dropping_it(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.create_note(type="topic", title="日本語のノート", author="nuru")
    assert result["path"] != "nuru/.md"
    assert result["path"].startswith("nuru/")
    assert result["path"] != f"nuru/{result['id'].lower()}.md"  # real transliteration happened


def test_create_note_falls_back_to_the_id_when_transliteration_yields_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.create_note(type="topic", title="🎉🎊✨", author="nuru")
    assert result["path"] == f"nuru/{result['id'].lower()}.md"


def test_create_note_still_rejects_a_genuinely_empty_title(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match="title is empty"):
        server.create_note(type="topic", title="   ", author="nuru")


def test_create_note_preserves_unicode_in_aliases(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.create_note(type="topic", title="日本語のノート", author="nuru")
    text = (tmp_path / result["path"]).read_text()
    assert "日本語のノート" in text
    assert "\\u" not in text


def test_create_note_rejects_a_user_folder_type_with_a_non_user_author(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    with pytest.raises(ToolError, match='only author="user" may create'):
        server.create_note(type="directives", title="X", author="nuru")


def test_create_note_accepts_a_user_folder_type_with_author_user(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    result = server.create_note(type="directives", title="X", author="user")
    assert result["path"] == "user/x.md"


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


def test_create_note_reports_an_index_integrity_error_clearly(tmp_path, monkeypatch):
    """Regression test: a sqlite3.IntegrityError from the index insert used
    to propagate unwrapped - not one of _tool's anticipated error types - so
    it reached the caller as a bare, message-less "Error executing tool
    create_note" crash instead of an explainable ValueError."""
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    def failing_insert_note(*args, **kwargs):
        raise sqlite3.IntegrityError("UNIQUE constraint failed: notes.path")

    monkeypatch.setattr(server.note_index, "insert_note", failing_insert_note)

    with pytest.raises(ToolError, match="ValueError") as exc_info:
        server.create_note(type="project", title="X", author="both")
    assert "already has an entry" in str(exc_info.value)
    assert not (tmp_path / "shared" / "x.md").exists()


def test_create_note_recreating_a_title_after_the_vault_was_cleared_externally(tmp_path, monkeypatch):
    """Regression test: emptying the vault outside the MCP left stale index
    rows behind (their files gone, but their paths still UNIQUE-constrained),
    so recreating a previously-used title failed - the exact path is covered
    by test_create_note_reports_an_index_integrity_error_clearly above; this
    checks reconciliation prevents the collision from happening at all."""
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    server.create_note(type="topic", title="Alpha Topic", author="nuru")

    (tmp_path / "nuru" / "alpha-topic.md").unlink()  # vault cleared outside the MCP

    result = server.create_note(type="topic", title="Alpha Topic", author="nuru")
    assert result["path"] == "nuru/alpha-topic.md"


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


def test_rename_or_move_file_keeps_an_indexed_note_reachable_by_id(tmp_path, monkeypatch):
    """Regression test: moving a note used to leave its index row pointing at
    the old path, so append_note("<id>", ...) failed with "indexed at
    <old path>, but that file no longer exists" even though the note still
    exists - just moved. This is what archive-by-move relies on."""
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    created = server.create_note(type="project", title="Move Target", author="both", body="Original.")

    new_path = server.rename_or_move_file(created["path"], "shared/moved-target.md")

    assert server.append_note(created["id"], "Still reachable.") == new_path
    assert (tmp_path / new_path).read_text().endswith("Original.\n\nStill reachable.")


def test_rename_or_move_file_survives_index_reconciliation_racing_the_move(tmp_path, monkeypatch):
    """Regression test: _open_index() reconciles against whatever's on disk
    at the moment it's called. rename_or_move_file used to physically move
    the file first and only then open the index to repoint the row - so
    reconciliation, running after the move, saw the *old* path already gone
    and deleted the row before update_path could repoint it, silently
    dropping the note from the index instead of moving it."""
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    created = server.create_note(type="project", title="X", author="both")

    server.rename_or_move_file(created["path"], "shared/moved.md")

    with server._open_index() as conn:
        assert server.note_index.note_exists(conn, created["id"]) is True
        assert server.note_index.get_path(conn, created["id"]) == "shared/moved.md"


def test_rename_or_move_file_of_a_non_indexed_file_is_a_no_op_on_the_index(tmp_path, monkeypatch):
    (tmp_path / "attachment.png").write_bytes(b"data")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    # Must not raise just because nothing in the index matches this path.
    assert server.rename_or_move_file("attachment.png", "moved.png") == "moved.png"


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


def test_delete_file_or_directory_removes_the_note_s_index_row(tmp_path, monkeypatch):
    """Regression test: deleting a note used to leave its index row behind,
    so append_note("<id>", ...) reported a stale "indexed at <path>, but
    that file no longer exists" instead of a clean "no note with id"."""
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    created = server.create_note(type="project", title="X", author="both")

    server.delete_file_or_directory(created["path"])

    with pytest.raises(ToolError, match="FileNotFoundError") as exc_info:
        server.append_note(created["id"], "content")
    assert "no longer exists" not in str(exc_info.value)


def test_delete_file_or_directory_removes_index_rows_for_notes_in_a_deleted_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    created = server.create_note(type="project", title="X", author="both")
    note_dir = (tmp_path / created["path"]).parent

    server.delete_file_or_directory(str(note_dir.relative_to(tmp_path)))

    with pytest.raises(ToolError, match="FileNotFoundError"):
        server.append_note(created["id"], "content")


def test_delete_file_or_directory_of_a_non_indexed_file_is_a_no_op_on_the_index(tmp_path, monkeypatch):
    (tmp_path / "attachment.png").write_bytes(b"data")
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)

    # Must not raise just because nothing in the index matches this path.
    assert server.delete_file_or_directory("attachment.png") == ["attachment.png"]


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
