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
