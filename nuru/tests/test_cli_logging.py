import json

from core.cli_logging import CLILogger


def log_file(log_dir):
    files = list(log_dir.glob("*.jsonl"))
    assert len(files) == 1
    return files[0]


def read_entries(log_dir):
    return [json.loads(line) for line in log_file(log_dir).read_text().splitlines()]


def test_creates_log_dir_if_missing(tmp_path):
    log_dir = tmp_path / "nested" / "logs"
    assert not log_dir.exists()

    CLILogger(log_dir, model="qwen3:8b", think=True, server_command="uv run obsidian-mcp")

    assert log_dir.is_dir()


def test_session_start_is_logged_on_construction(tmp_path):
    logger = CLILogger(tmp_path, model="qwen3:8b", think=True, server_command="uv run obsidian-mcp")

    entries = read_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["event"] == "session_start"
    assert entries[0]["session_id"] == logger.session_id
    assert entries[0]["model"] == "qwen3:8b"
    assert entries[0]["think"] is True
    assert entries[0]["server_command"] == "uv run obsidian-mcp"


def test_turn_start_and_end_use_incrementing_turn_ids(tmp_path):
    logger = CLILogger(tmp_path, model="m", think=False, server_command="cmd")

    first = logger.turn_start("hello")
    logger.turn_end(first)
    second = logger.turn_start("world")
    logger.turn_end(second)

    assert first == "t-1"
    assert second == "t-2"

    entries = read_entries(tmp_path)
    assert [e["event"] for e in entries] == ["session_start", "turn_start", "turn_end", "turn_start", "turn_end"]


def test_turn_start_truncates_input_preview_at_64_chars(tmp_path):
    logger = CLILogger(tmp_path, model="m", think=False, server_command="cmd")
    long_text = "a" * 100

    logger.turn_start(long_text)

    entries = read_entries(tmp_path)
    preview = entries[1]["input_preview"]
    assert preview["text"] == "a" * 64 + "…"
    assert preview["truncated"] is True


def test_turn_start_does_not_truncate_short_input(tmp_path):
    logger = CLILogger(tmp_path, model="m", think=False, server_command="cmd")

    logger.turn_start("short input")

    entries = read_entries(tmp_path)
    preview = entries[1]["input_preview"]
    assert preview["text"] == "short input"
    assert preview["truncated"] is False


def test_model_lifecycle_is_logged_once_even_if_called_repeatedly(tmp_path):
    logger = CLILogger(tmp_path, model="m", think=False, server_command="cmd")

    logger.model_lifecycle(
        True, {"parameter_size": "8B", "quantization_level": "Q4_K_M", "family": "qwen3", "supports_thinking": True}
    )
    logger.model_lifecycle(False, {"parameter_size": "8B"})

    entries = read_entries(tmp_path)
    lifecycle_entries = [e for e in entries if e["event"] == "model_lifecycle"]
    assert len(lifecycle_entries) == 1
    assert lifecycle_entries[0]["server_spawned"] is True
    assert lifecycle_entries[0]["parameter_size"] == "8B"
    assert lifecycle_entries[0]["supports_thinking"] is True


def test_model_lifecycle_handles_missing_model_info(tmp_path):
    logger = CLILogger(tmp_path, model="m", think=False, server_command="cmd")

    logger.model_lifecycle(False, None)

    entries = read_entries(tmp_path)
    lifecycle = entries[1]
    assert lifecycle["server_spawned"] is False
    assert lifecycle["parameter_size"] is None


def test_model_call_accumulates_tokens_processed_across_calls(tmp_path):
    logger = CLILogger(tmp_path, model="m", think=False, server_command="cmd")
    turn_id = logger.turn_start("hi")

    logger.model_call(
        turn_id=turn_id,
        duration_ms=10.0,
        prompt_tokens=100,
        completion_tokens=20,
        stop_reason="stop",
        tool_calls_requested=[],
        output_text="hello there",
    )
    logger.model_call(
        turn_id=turn_id,
        duration_ms=15.0,
        prompt_tokens=150,
        completion_tokens=30,
        stop_reason="stop",
        tool_calls_requested=["list_notes"],
        output_text="more output",
    )

    entries = read_entries(tmp_path)
    calls = [e for e in entries if e["event"] == "model_call"]

    assert calls[0]["session_tokens_processed"] == 120
    assert calls[0]["session_context_tokens"] == 120
    assert calls[1]["session_tokens_processed"] == 300
    assert calls[1]["session_context_tokens"] == 180
    assert calls[1]["tool_calls_requested"] == ["list_notes"]


def test_model_call_truncates_output_preview(tmp_path):
    logger = CLILogger(tmp_path, model="m", think=False, server_command="cmd")
    turn_id = logger.turn_start("hi")

    logger.model_call(
        turn_id=turn_id,
        duration_ms=1.0,
        prompt_tokens=1,
        completion_tokens=1,
        stop_reason="stop",
        tool_calls_requested=[],
        output_text="b" * 70,
    )

    entries = read_entries(tmp_path)
    preview = entries[-1]["output_preview"]
    assert preview["text"] == "b" * 64 + "…"
    assert preview["truncated"] is True


def test_tool_call_logs_status_and_error(tmp_path):
    logger = CLILogger(tmp_path, model="m", think=False, server_command="cmd")
    turn_id = logger.turn_start("hi")

    logger.tool_call(turn_id, "list_notes", {"folder": "Planning"}, "ok", None, 12.5)
    logger.tool_call(turn_id, "read_note", {"name": "x"}, "error", "not found", 3.2)

    entries = read_entries(tmp_path)
    tool_entries = [e for e in entries if e["event"] == "tool_call"]

    assert tool_entries[0]["status"] == "ok"
    assert tool_entries[0]["error"] is None
    assert tool_entries[0]["arguments"] == {"folder": "Planning"}
    assert tool_entries[1]["status"] == "error"
    assert tool_entries[1]["error"] == "not found"


def test_session_end_reports_final_totals(tmp_path):
    logger = CLILogger(tmp_path, model="m", think=False, server_command="cmd")
    turn_id = logger.turn_start("hi")
    logger.model_call(
        turn_id=turn_id,
        duration_ms=1.0,
        prompt_tokens=100,
        completion_tokens=50,
        stop_reason="stop",
        tool_calls_requested=[],
        output_text="ok",
    )

    logger.session_end(reason="/exit", models_unloaded=True, server_stopped=True)

    entries = read_entries(tmp_path)
    end = entries[-1]
    assert end["event"] == "session_end"
    assert end["reason"] == "/exit"
    assert end["session_tokens_processed"] == 150
    assert end["session_context_tokens"] == 150


def test_logging_failure_does_not_raise(tmp_path, monkeypatch):
    logger = CLILogger(tmp_path, model="m", think=False, server_command="cmd")

    def broken_info(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(logger._logger, "info", broken_info)

    logger.turn_start("hi")  # must not raise


def test_close_releases_the_file_and_leaves_other_sessions_logging(tmp_path):
    import logging

    first = CLILogger(tmp_path / "a", model="m", think=False, server_command="cmd")
    second = CLILogger(tmp_path / "b", model="m", think=False, server_command="cmd")
    (handler,) = first._logger.handlers
    registered = set(logging.root.manager.loggerDict)

    first.close()
    first.close()  # again: nothing left to close
    first.turn_start("after close")  # dropped, not raised
    second.turn_start("still here")

    assert handler.stream is None and first._logger.handlers == []
    assert [e["event"] for e in read_entries(tmp_path / "a")] == ["session_start"]
    assert [e["event"] for e in read_entries(tmp_path / "b")] == ["session_start", "turn_start"]
    assert not any(name.startswith("cli_logging") for name in registered)  # nothing for the registry to keep


def test_close_survives_a_handler_that_fails_to_close(tmp_path):
    logger = CLILogger(tmp_path, model="m", think=False, server_command="cmd")
    (handler,) = logger._logger.handlers
    handler.close = lambda: (_ for _ in ()).throw(OSError("disk gone"))

    logger.close()

    assert logger._logger.handlers == []


def test_a_log_file_that_cannot_open_logs_nothing_and_still_closes(tmp_path, monkeypatch):
    import logging

    def refuse(*args, **kwargs):
        raise PermissionError("read-only disk")

    monkeypatch.setattr(logging, "FileHandler", refuse)
    logger = CLILogger(tmp_path, model="m", think=False, server_command="cmd")
    logger.turn_start("hi")
    logger.close()

    assert list(tmp_path.glob("*.jsonl")) == [] and logger._logger.handlers == []


def test_stewardship_logs_counts_without_the_diagnostics(tmp_path):
    logger = CLILogger(tmp_path, model="m", think=False, server_command="cmd")

    logger.stewardship({"snapshot": "s1", "packages": 2, "complete": True, "counts": {"ok": 3},
                        "finding_count": 1, "diagnostics": ["private detail", "another"]})

    entry = read_entries(tmp_path)[-1]
    assert entry["event"] == "stewardship" and entry["diagnostic_count"] == 2 and entry["model_calls"] == 0
    assert "private detail" not in json.dumps(entry)
