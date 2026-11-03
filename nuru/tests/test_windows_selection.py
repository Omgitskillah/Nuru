"""What CI runs on Windows (tools/windows_selection.py): that this repository's selection holds every test
that names the platform, and the checker itself, on real coverage data and on test files written here."""
import importlib.util
import json
from pathlib import Path

from coverage import CoverageData

_PATH = Path(__file__).resolve().parents[2] / "tools" / "windows_selection.py"
_spec = importlib.util.spec_from_file_location("windows_selection", _PATH)
ws = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ws)


def test_this_repositorys_selection_is_whole_by_what_its_tests_say():
    assert ws.problems(ws.entries(), set(ws.platform_tests())) == []


def test_the_selection_has_no_entry_twice():
    assert len(ws.entries()) == len(set(ws.entries()))


def test_an_entry_runs_its_file_its_class_its_test_and_each_parameter_but_no_namesake():
    chosen = ["a/test_x.py::test_one", "a/test_y.py"]
    assert ws.selected("a/test_x.py::test_one", chosen)
    assert ws.selected("a/test_x.py::test_one[nt-1]", chosen)
    assert ws.selected("a/test_y.py::TestZ::test_two", chosen)
    assert not ws.selected("a/test_x.py::test_one_more", chosen)
    assert not ws.selected("a/test_x.py::test_two", chosen)


def repo(tmp_path, tests: dict[str, str], plugin_tests: dict[str, str] = {}, windows_script=""):
    for name, source in tests.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(source, encoding="utf-8")
    plugin = tmp_path / "plugins" / "obsidian"
    for name, source in plugin_tests.items():
        (plugin / name).parent.mkdir(parents=True, exist_ok=True)
        (plugin / name).write_text(source, encoding="utf-8")
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / "package.json").write_text(json.dumps({"scripts": {"test:windows": f"node --test {windows_script}"}}))
    return tmp_path


def test_a_test_naming_the_platform_in_its_body_decorator_or_class_is_found(tmp_path):
    root = repo(tmp_path, {"nuru/tests/test_a.py": '''
import os, sys, pytest

def test_plain():
    assert 1

@pytest.mark.skipif(sys.platform != "win32", reason="Windows")
def test_skipped_elsewhere():
    pass

def test_patches(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")

class TestPaths:
    def test_inside(self):
        assert os.name

    def test_not(self):
        pass

def helper():
    return sys.platform
''', "servers/obsidian/tests/test_b.py": '''
import sys, pytest
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows")

def test_any():
    pass
''', "nuru/tests/helpers.py": "import sys\nX = sys.platform\n"})
    assert ws.platform_tests(root) == [
        "nuru/tests/test_a.py::test_skipped_elsewhere", "nuru/tests/test_a.py::test_patches",
        "nuru/tests/test_a.py::TestPaths::test_inside", "servers/obsidian/tests/test_b.py"]


def test_what_isnt_selected_or_names_nothing_is_reported(tmp_path):
    root = repo(tmp_path, {"nuru/tests/test_a.py": "def test_x():\n    pass\n"},
                {"test/spawns.test.js": "const { spawn } = require('child_process');",
                 "test/paths.test.js": "if (process.platform === 'win32') {}",
                 "test/e2e/serve.test.js": "require('node:child_process')",
                 "test/view.test.js": "test('renders', () => {});"},
                windows_script="test/paths.test.js")
    assert ws.problems(["nuru/tests/test_a.py::test_x", "nuru/tests/test_gone.py"],
                       {"nuru/tests/test_a.py::test_x", "nuru/tests/test_a.py::test_y[1]"}, root=root) == [
        "needs Windows, not in tools/windows_tests.txt: nuru/tests/test_a.py::test_y[1]",
        "tools/windows_tests.txt names no test file: nuru/tests/test_gone.py",
        "starts processes or names the platform, not in test:windows: test/spawns.test.js",
        "starts processes or names the platform, not in test:windows: test/e2e/serve.test.js"]


def coverage(path: Path, lines: dict[str, dict[int, list[str]]]) -> Path:
    """Coverage data as a run records it: per file, each line and the tests (contexts) that ran it."""
    data = CoverageData(str(path))
    for context in sorted({c for f in lines.values() for cs in f.values() for c in cs}):
        data.set_context(context)
        data.add_lines({f: [n for n, cs in by_line.items() if context in cs] for f, by_line in lines.items()})
    data.write()
    return path


def test_the_lines_only_windows_ran_come_with_the_tests_that_ran_them(tmp_path):
    windows = coverage(tmp_path / "windows.coverage", {
        "servers\\obsidian\\src\\m.py": {1: ["t.py::test_any|run"], 5: ["t.py::test_lock|run", "t.py::test_both[a]|setup"]},
        "nuru\\src\\core\\x.py": {2: [""], 3: ["t.py::test_any|run"], 4: ["t.py::test_new_file|run"]}})
    local = coverage(tmp_path / "local.coverage", {
        "servers/obsidian/src/m.py": {1: ["t.py::test_any|run"], 9: ["t.py::test_posix|run"]},
        f"{ws.ROOT.as_posix()}/nuru/src/core/x.py": {3: ["t.py::test_any|run"]}})
    assert ws.windows_only_lines(windows, local) == {
        ("servers/obsidian/src/m.py", 5): {"t.py::test_lock", "t.py::test_both[a]"},
        ("nuru/src/core/x.py", 2): set(), ("nuru/src/core/x.py", 4): {"t.py::test_new_file"}}


def test_a_windows_only_line_needs_one_selected_test_and_an_import_time_line_none(tmp_path):
    root = repo(tmp_path, {"t.py": ""})
    lines = {("m.py", 5): {"t.py::test_lock", "t.py::test_both[a]"}, ("m.py", 7): {"t.py::test_other"}, ("m.py", 1): set()}
    assert ws.problems(["t.py::test_both"], set(), lines, root) == [
        "m.py:7 runs only on Windows, in no selected test; e.g. t.py::test_other"]


def test_the_command_checks_against_coverage_when_given_both_runs(tmp_path, capsys):
    windows = coverage(tmp_path / "w", {"nuru/src/core/x.py": {1: ["nuru/tests/test_q.py::test_only_windows|run"]}})
    local = coverage(tmp_path / "l", {"nuru/src/core/x.py": {}})
    assert ws.main([]) == 0 and "the Windows selection is whole" in capsys.readouterr().out
    assert ws.main(["--windows", str(windows), "--local", str(local)]) == 1
    assert "nuru/src/core/x.py:1 runs only on Windows, in no selected test; e.g. nuru/tests/test_q.py::test_only_windows" \
        in capsys.readouterr().out


def test_the_two_runs_go_together(capsys):
    import pytest
    with pytest.raises(SystemExit) as exit:
        ws.main(["--windows", "w"])
    assert exit.value.code == 2 and "go together" in capsys.readouterr().err


def test_run_as_a_command_it_exits_with_its_verdict(monkeypatch):
    import runpy
    import pytest
    monkeypatch.setattr("sys.argv", ["windows_selection.py"])
    with pytest.raises(SystemExit) as exit:
        runpy.run_path(str(_PATH), run_name="__main__")
    assert exit.value.code == 0
