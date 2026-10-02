from core import forced_schema
from core.forced_schema import CANNOT_DO, arguments_schema, validate

WRITE = {"properties": {
    "content": {"title": "Content", "type": "string"},
    "note": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
    "type": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
    "author": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
    "supersedes": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
    "targets": {"anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}], "default": None}},
    "required": ["content"], "title": "write_noteArguments", "type": "object"}
IDS = ["01M2X9M15NDE1FJYDMC0SPGRVA", "01M2NT6HP8THJKVENCBHBQBN5W"]


def test_a_note_id_can_only_be_one_a_search_returned():
    schema = arguments_schema("write_note", WRITE, IDS)
    assert validate({"content": "x", "supersedes": IDS[0]}, schema) == []
    assert validate({"content": "x", "supersedes": "01AAAAAAAAAAAAAAAAAAAAAAAA"}, schema)
    assert validate({"content": "x", "supersedes": "priya-raman"}, schema)


def test_the_note_itself_may_be_a_title_for_a_new_note():
    schema = arguments_schema("write_note", WRITE, IDS)
    assert validate({"content": "x", "note": "Garden Plan", "type": "topic", "author": "nuru"}, schema) == []


def test_the_way_out_is_always_there():
    schema = arguments_schema("write_note", WRITE, IDS)
    assert validate({"cannot_do": "no such note"}, schema) == []
    assert validate({"cannot_do": "x", "content": "y"}, schema)  # not a mix of both


def test_a_required_id_with_nothing_to_choose_from_leaves_only_the_way_out(monkeypatch):
    monkeypatch.setitem(forced_schema.NOTE_ID_FIELDS, "pick_note", ("id",))
    schema = {"properties": {"id": {"type": "string"}}, "required": ["id"], "type": "object"}
    assert arguments_schema("pick_note", schema, []) == {"anyOf": [CANNOT_DO]}


def test_optional_id_fields_take_known_ids_or_nothing():
    schema = arguments_schema("write_note", WRITE, IDS)
    base = {"content": "c", "note": "t", "type": "correction", "author": "nuru"}
    assert validate({**base, "targets": [IDS[1]]}, schema) == []
    assert validate({**base, "targets": None, "supersedes": None}, schema) == []
    assert validate({**base, "targets": ["01AAAAAAAAAAAAAAAAAAAAAAAA"]}, schema)
    assert validate({**base, "supersedes": "made-up"}, schema)


def test_optional_id_fields_are_dropped_when_no_search_returned_any():
    schema = arguments_schema("write_note", WRITE, [])
    arguments = schema["anyOf"][0]
    assert "targets" not in arguments["properties"] and "supersedes" not in arguments["properties"]
    assert validate({"content": "c", "note": "t", "type": "topic", "author": "nuru", "targets": [IDS[0]]}, schema)


def test_tools_without_ids_keep_their_schema_and_refuse_extra_fields():
    schema = arguments_schema("rename_or_move_file", {
        "properties": {"source_path": {"type": "string"}, "destination_path": {"type": "string"}},
        "required": ["source_path", "destination_path"], "type": "object"}, IDS)
    assert validate({"source_path": "nuru/x.md", "destination_path": "trash"}, schema) == []
    assert validate({"source_path": "nuru/x.md", "destination_path": "trash", "force": True}, schema)
    assert validate({}, schema)


def test_the_input_schema_is_not_modified():
    before = repr(WRITE)
    arguments_schema("write_note", WRITE, IDS)
    assert repr(WRITE) == before
