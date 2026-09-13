import asyncio
import re
from dataclasses import dataclass
from typing import Any

import pytest

from access_logging import AccessLogMiddleware


@dataclass
class FakeContext:
    """Minimal stand-in for ServerRequestContext: the middleware only reads
    `.method` and `.params`."""

    method: str
    params: dict[str, Any] | None


def run(coro):
    return asyncio.run(coro)


def call_next_ok(result: Any = None):
    async def _call_next(ctx):
        return result

    return _call_next


def call_next_raises(exc: Exception):
    async def _call_next(ctx):
        raise exc

    return _call_next


def log_files(log_dir):
    return sorted(log_dir.glob("*.log"))


def read_lines(path):
    return path.read_text().splitlines()


def test_creates_log_dir_if_missing(tmp_path):
    log_dir = tmp_path / "nested" / "logs"
    assert not log_dir.exists()

    AccessLogMiddleware(log_dir)

    assert log_dir.is_dir()


def test_initialize_creates_session_file_named_with_id_date_and_client(tmp_path):
    middleware = AccessLogMiddleware(tmp_path)
    ctx = FakeContext(
        method="initialize",
        params={"clientInfo": {"name": "mcp-inspector"}},
    )

    run(middleware(ctx, call_next_ok()))

    files = log_files(tmp_path)
    assert len(files) == 1
    assert re.match(r"^1_\d{8}-\d{6}_mcp-inspector\.log$", files[0].name)


def test_missing_client_name_falls_back_to_unknown(tmp_path):
    middleware = AccessLogMiddleware(tmp_path)
    ctx = FakeContext(method="initialize", params={})

    run(middleware(ctx, call_next_ok()))

    files = log_files(tmp_path)
    assert re.match(r"^1_\d{8}-\d{6}_unknown\.log$", files[0].name)


def test_no_client_info_at_all_falls_back_to_unknown(tmp_path):
    middleware = AccessLogMiddleware(tmp_path)
    ctx = FakeContext(method="initialize", params=None)

    run(middleware(ctx, call_next_ok()))

    files = log_files(tmp_path)
    assert re.match(r"^1_\d{8}-\d{6}_unknown\.log$", files[0].name)


def test_sanitizes_unsafe_characters_in_client_name(tmp_path):
    middleware = AccessLogMiddleware(tmp_path)
    ctx = FakeContext(
        method="initialize",
        params={"clientInfo": {"name": "My Client/v1 (beta)!"}},
    )

    run(middleware(ctx, call_next_ok()))

    files = log_files(tmp_path)
    assert len(files) == 1
    # Only safe filename characters remain, no path separators or spaces.
    assert re.match(r"^1_\d{8}-\d{6}_My-Client-v1-beta\.log$", files[0].name)


def test_each_initialize_starts_a_new_session_file_with_incrementing_id(tmp_path):
    middleware = AccessLogMiddleware(tmp_path)

    run(middleware(FakeContext("initialize", {"clientInfo": {"name": "alice"}}), call_next_ok()))
    run(middleware(FakeContext("initialize", {"clientInfo": {"name": "bob"}}), call_next_ok()))

    files = log_files(tmp_path)
    assert len(files) == 2
    assert files[0].name.startswith("1_")
    assert "alice" in files[0].name
    assert files[1].name.startswith("2_")
    assert "bob" in files[1].name


def test_session_id_continues_across_middleware_instances_sharing_a_dir(tmp_path):
    first = AccessLogMiddleware(tmp_path)
    run(first(FakeContext("initialize", {"clientInfo": {"name": "alice"}}), call_next_ok()))

    second = AccessLogMiddleware(tmp_path)
    run(second(FakeContext("initialize", {"clientInfo": {"name": "bob"}}), call_next_ok()))

    files = log_files(tmp_path)
    assert len(files) == 2
    assert files[1].name.startswith("2_")


def test_request_before_any_initialize_still_gets_a_session_file(tmp_path):
    middleware = AccessLogMiddleware(tmp_path)
    ctx = FakeContext(method="tools/list", params={})

    run(middleware(ctx, call_next_ok()))

    files = log_files(tmp_path)
    assert len(files) == 1
    assert "unknown" in files[0].name


def test_log_line_format_for_successful_call(tmp_path):
    middleware = AccessLogMiddleware(tmp_path)
    init_ctx = FakeContext("initialize", {"clientInfo": {"name": "alice"}})
    run(middleware(init_ctx, call_next_ok()))

    call_ctx = FakeContext("tools/call", {"name": "list_notes", "arguments": {}})
    run(middleware(call_ctx, call_next_ok("some result")))

    lines = read_lines(log_files(tmp_path)[0])
    timestamp_re = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}"

    assert re.match(
        timestamp_re + r" -> \{'lt': 'tools/call', 'params': \{'name': 'list_notes', 'arguments': \{\}\}\}$",
        lines[2],
    )
    assert re.match(timestamp_re + r" <- \{'lt': 'tools/call', 'result': 'ok'\}$", lines[3])


def test_failed_call_is_logged_as_error_and_exception_still_propagates(tmp_path):
    middleware = AccessLogMiddleware(tmp_path)
    run(middleware(FakeContext("initialize", {"clientInfo": {"name": "alice"}}), call_next_ok()))

    ctx = FakeContext("tools/call", {"name": "read_note", "arguments": {"name": "x"}})

    with pytest.raises(ValueError, match="boom"):
        run(middleware(ctx, call_next_raises(ValueError("boom"))))

    lines = read_lines(log_files(tmp_path)[0])
    assert "'lt': 'tools/call', 'result': 'error', 'error': 'boom'" in lines[3]


def test_call_next_return_value_is_passed_through(tmp_path):
    middleware = AccessLogMiddleware(tmp_path)
    run(middleware(FakeContext("initialize", {}), call_next_ok()))

    result = run(middleware(FakeContext("tools/call", {}), call_next_ok({"ok": True})))

    assert result == {"ok": True}


def test_logging_failure_does_not_break_the_request(tmp_path, monkeypatch):
    middleware = AccessLogMiddleware(tmp_path)
    run(middleware(FakeContext("initialize", {}), call_next_ok()))

    def broken_info(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(middleware._logger, "info", broken_info)

    result = run(middleware(FakeContext("tools/call", {}), call_next_ok("fine")))

    assert result == "fine"
