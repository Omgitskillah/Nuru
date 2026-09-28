from core.forced_schema import CANNOT_DO, arguments_schema, validate

APPEND = {"properties": {"id": {"title": "Id", "type": "string"}, "content": {"title": "Content", "type": "string"}},
          "required": ["id", "content"], "title": "append_noteArguments", "type": "object"}
CREATE = {"properties": {
    "type": {"type": "string"}, "title": {"type": "string"}, "author": {"type": "string"},
    "supersedes": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
    "targets": {"anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}], "default": None}},
    "required": ["type", "title", "author"], "type": "object"}
IDS = ["01M2X9M15NDE1FJYDMC0SPGRVA", "01M2NT6HP8THJKVENCBHBQBN5W"]


def test_a_note_id_can_only_be_one_a_search_returned():
    schema = arguments_schema("append_note", APPEND, IDS)
    assert validate({"id": IDS[0], "content": "x"}, schema) == []
    assert validate({"id": "01AAAAAAAAAAAAAAAAAAAAAAAA", "content": "x"}, schema)
    assert validate({"id": "priya-raman", "content": "x"}, schema)


def test_the_way_out_is_always_there():
    schema = arguments_schema("append_note", APPEND, IDS)
    assert validate({"cannot_do": "no such note"}, schema) == []
    assert validate({"cannot_do": "x", "id": IDS[0]}, schema)  # not a mix of both


def test_a_required_id_with_nothing_to_choose_from_leaves_only_the_way_out():
    assert arguments_schema("append_note", APPEND, []) == {"anyOf": [CANNOT_DO]}


def test_optional_id_fields_take_known_ids_or_nothing():
    schema = arguments_schema("create_note", CREATE, IDS)
    base = {"type": "correction", "title": "t", "author": "user"}
    assert validate({**base, "targets": [IDS[1]]}, schema) == []
    assert validate({**base, "targets": None, "supersedes": None}, schema) == []
    assert validate({**base, "targets": ["01AAAAAAAAAAAAAAAAAAAAAAAA"]}, schema)
    assert validate({**base, "supersedes": "made-up"}, schema)


def test_optional_id_fields_are_dropped_when_no_search_returned_any():
    schema = arguments_schema("create_note", CREATE, [])
    arguments = schema["anyOf"][0]
    assert "targets" not in arguments["properties"] and "supersedes" not in arguments["properties"]
    assert validate({"type": "topic", "title": "t", "author": "nuru", "targets": [IDS[0]]}, schema)


def test_tools_without_ids_keep_their_schema_and_refuse_extra_fields():
    schema = arguments_schema("add_to_daily", {"properties": {"text": {"type": "string"}}, "required": ["text"],
                                               "type": "object"}, IDS)
    assert validate({"text": "hi"}, schema) == []
    assert validate({"text": "hi", "path": "user/x.md"}, schema)
    assert validate({}, schema)


def test_the_input_schema_is_not_modified():
    before = repr(APPEND)
    arguments_schema("append_note", APPEND, IDS)
    assert repr(APPEND) == before
