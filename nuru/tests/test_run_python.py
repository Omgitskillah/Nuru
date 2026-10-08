import json

from core import session
from core.providers.mcp_tools import ToolResult
from core.session import python_server_params  # the real one: conftest stubs session's for every other test


SERVER_TOOL = {"type": "function", "function": {"name": "run_python", "description": "server's own", "parameters": {
    "type": "object", "properties": {"code": {"type": "string"}, "stdin": {"type": "string"},
                                     "timeout_ms": {"type": "integer"}}, "required": ["code"]}}}


def test_the_model_sees_code_and_notes_not_stdin_or_timeout():
    (tool,) = session._hide_injected_args([SERVER_TOOL])

    assert set(tool["function"]["parameters"]["properties"]) == {"code", "notes"}
    assert tool["function"]["parameters"]["required"] == ["code"]
    assert "json.load(sys.stdin)" in tool["function"]["description"]


def test_notes_are_read_by_nuru_and_reach_the_script_as_stdin_json():
    read = {"nuru/a.md": ToolResult("ppm,adc\n0,812\n"), "nuru/b.md": ToolResult("text b")}

    request, refused = session._python_request({"code": "print(1)", "notes": ["nuru/a.md", "nuru/b.md"]}, read.get)

    assert refused is None
    assert request["code"] == "print(1)"
    assert json.loads(request["stdin"]) == {"nuru/a.md": "ppm,adc\n0,812\n", "nuru/b.md": "text b"}


def test_without_notes_there_is_no_stdin():
    request, refused = session._python_request({"code": "print(1)"}, lambda note: None)

    assert (request, refused) == ({"code": "print(1)"}, None)


def test_a_note_that_cant_be_read_stops_the_script_before_it_runs():
    request, refused = session._python_request(
        {"code": "print(1)", "notes": ["user/secret.md"]}, lambda note: ToolResult("refused: user/", is_error=True))

    assert request is None
    assert "user/secret.md" in refused and "refused: user/" in refused


def test_long_output_is_cut_to_what_the_context_has_room_for():
    long = "x" * (session.PYTHON_OUTPUT_CHARS + 50)
    text = json.dumps({"ok": True, "status": "SUCCESS", "stdout": long, "stderr": ""})

    data = json.loads(session._python_result(text))

    assert data["stdout"].startswith("x" * session.PYTHON_OUTPUT_CHARS)
    assert data["stdout"].endswith("[... 50 more characters]")
    assert data["ok"] is True and data["stderr"] == ""


def test_nuru_starts_without_run_python_when_it_has_no_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "ENV_DIR", tmp_path)

    assert python_server_params() is None


def test_nuru_starts_without_run_python_when_docker_does_not_answer(tmp_path, monkeypatch):
    (tmp_path / ".env.python").write_text(
        "PYTHON_MCP_DOCKER=/nonexistent/docker\nPYTHON_MCP_DOCKER_SOCKET=/nonexistent.sock\n"
        f"PYTHON_MCP_IMAGE=sha256:{'a' * 64}\n")
    monkeypatch.setattr(session, "ENV_DIR", tmp_path)

    assert python_server_params() is None
