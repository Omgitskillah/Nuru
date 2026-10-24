"""The model's tool contract, shared by prompt validation and MCP discovery."""

# A server's new tools reach the model only after being added here. Harness tools
# (Laya's decisions) are deliberately excluded.
MODEL_TOOLS = frozenset(
    {
        "browse", "find_in_notes", "note_info", "read_note", "search", "tags",
        "rename_or_move_file", "write_note",
        "calculate", "statistics",
        # Optional: offered only when the Docker-backed server starts.
        "run_python",
    }
)

# Names from the old assistant prompts: exact identifiers, never inferred from prose.
RETIRED_TOOLS = frozenset(
    {
        "search_notes", "browse_notes", "append_note", "create_note",
        "add_to_daily", "list_notes", "ask_an_expert",
        "notes_with_tag", "semantic_search", "get_links", "note_tags", "all_tags",
        "note_todos", "list_files", "list_files_in_directory",
        "archive_note", "delete_file_or_directory",
    }
)
