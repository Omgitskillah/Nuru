"""F13's coverage gate (tools/coverage_gate.py) on real report formats."""
import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "tools" / "coverage_gate.py"
_spec = importlib.util.spec_from_file_location("coverage_gate", _PATH)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def python_report(tmp_path, root="src", **files):
    """A coverage JSON report, and the source root it covers: name -> (statements, covered)."""
    (tmp_path / root).mkdir(exist_ok=True)
    for name in files:
        (tmp_path / root / f"{name}.py").write_text("x = 1\n")
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"files": {f"{root}/{name}.py": {"summary": {"num_statements": n, "covered_lines": c}}
                                            for name, (n, c) in files.items()}}))
    return report


def check(tmp_path, monkeypatch, *argv):
    monkeypatch.chdir(tmp_path)
    return gate.main(list(argv))


# ---------------------------------------------------------------- the rule
def test_small_modules_need_every_line_and_large_ones_more_than_90_percent(tmp_path, monkeypatch, capsys):
    report = python_report(tmp_path, small_full=(150, 150), small_short=(150, 149), large_ok=(1000, 901),
                           large_at_90=(1000, 900), empty=(0, 0))
    assert check(tmp_path, monkeypatch, "--python", str(report), "--python-root", "src") == 1
    assert capsys.readouterr().out.splitlines() == [
        "src/large_at_90.py: 900/1000 lines, 90.0%; needs above 90%",
        "src/small_short.py: 149/150 lines, 99.3%; needs 100% (small)",
        "3 of 5 modules meet the coverage rule"]


def test_windows_paths_in_a_python_report_are_matched_to_the_sources(tmp_path, monkeypatch, capsys):
    report = python_report(tmp_path, ok=(10, 10))
    report.write_text(report.read_text().replace("src/ok.py", "src\\\\ok.py"))
    assert check(tmp_path, monkeypatch, "--python", str(report), "--python-root", "src") == 0
    assert capsys.readouterr().out == "1 of 1 modules meet the coverage rule\n"


# ---------------------------------------------------------------- Python reports it can't judge from
@pytest.mark.parametrize("text, problem", [("{not json", "isn't a coverage JSON report"),
                                           ('{"files": {}}', "has no files in it"),
                                           ('{"files": {"src/a.py": {"summary": {}}}}', "isn't a coverage JSON report"),
                                           ('{"totals": {}}', "isn't a coverage JSON report")])
def test_a_malformed_or_empty_python_report_fails_the_gate(tmp_path, monkeypatch, capsys, text, problem):
    (tmp_path / "coverage.json").write_text(text)
    assert check(tmp_path, monkeypatch, "--python", "coverage.json", "--python-root", "src") == 2
    assert problem in capsys.readouterr().err


def test_a_source_file_missing_from_the_python_report_fails_the_gate(tmp_path, monkeypatch, capsys):
    report = python_report(tmp_path, imported=(10, 10))
    (tmp_path / "src" / "never_imported.py").write_text("x = 1\n")
    assert check(tmp_path, monkeypatch, "--python", str(report), "--python-root", "src") == 2
    assert "leaves out 1 source file(s), e.g. src/never_imported.py" in capsys.readouterr().err


# ---------------------------------------------------------------- the plugin's lcov reports
def plugin(tmp_path, **files):
    root = tmp_path / "plugin"
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def lcov(path, records):
    """Write an lcov report: {source: {line: hits}}, each record complete as Node writes it."""
    lines = []
    for name, hits in records.items():
        lines += [f"SF:{name}", *(f"DA:{n},{h}" for n, h in sorted(hits.items())),
                  f"LF:{len(hits)}", f"LH:{sum(1 for h in hits.values() if h)}", "end_of_record"]
    path.write_text("\n".join(lines) + "\n")
    return path


SOURCE = '// header comment\n"use strict";\n/* a block\n   of words, not code\n*/\nfunction a() {\n  return "// not a comment";\n}\n\nif (process.platform === "win32") a(); // Windows\n'
# Code lines: 2, 6, 7, 8, 10. Node marks every line run, comments too, unless code around them didn't.


def test_code_lines_skip_blank_lines_and_comments_but_not_strings_that_look_like_them():
    assert gate.code_lines(SOURCE) == [2, 6, 7, 8, 10]
    assert gate.code_lines('const s = "/* not a comment */"; /* one */ x();\nconst t = `a\\`b`; // c\n') == [1, 2]


def test_a_line_run_on_either_system_counts_and_comments_never_do(tmp_path, monkeypatch, capsys):
    root = plugin(tmp_path, **{"lib/a.js": SOURCE})
    every = {n: 1 for n in range(1, 11)}
    ubuntu = lcov(tmp_path / "ubuntu.info", {"lib/a.js": {**every, 10: 0}})  # the Windows-only line didn't run here
    # Another system's checkout: an absolute Windows path to the same file.
    windows = lcov(tmp_path / "windows.info", {"D:\\a\\Nuru\\plugin\\lib\\a.js": {**every, 2: 0, 3: 0, 4: 0}})
    argv = ["--js-root", str(root), "--js-source", "lib/*.js"]
    assert check(tmp_path, monkeypatch, "--lcov", str(ubuntu), *argv) == 1
    assert capsys.readouterr().out.splitlines()[0] == "lib/a.js: 4/5 lines, 80.0%; needs 100% (small)"
    assert check(tmp_path, monkeypatch, "--lcov", str(ubuntu), "--lcov", str(windows), *argv) == 0
    assert capsys.readouterr().out == "1 of 1 modules meet the coverage rule\n"


def test_tests_and_files_outside_the_sources_are_ignored(tmp_path, monkeypatch, capsys):
    root = plugin(tmp_path, **{"lib/a.js": "x();\n", "test/a.test.js": "y();\n"})
    report = lcov(tmp_path / "lcov.info", {"lib/a.js": {1: 1}, "test/a.test.js": {1: 0}, "node_modules/x/i.js": {1: 0}})
    assert check(tmp_path, monkeypatch, "--lcov", str(report), "--js-root", str(root), "--js-source", "lib/*.js") == 0
    assert capsys.readouterr().out == "1 of 1 modules meet the coverage rule\n"


def test_a_source_no_test_loads_fails_the_gate(tmp_path, monkeypatch, capsys):
    root = plugin(tmp_path, **{"lib/a.js": "x();\n", "lib/unloaded.js": "y();\n"})
    report = lcov(tmp_path / "lcov.info", {"lib/a.js": {1: 1}})
    assert check(tmp_path, monkeypatch, "--lcov", str(report), "--js-root", str(root), "--js-source", "lib/*.js") == 2
    assert "no lcov report has lib/unloaded.js: no test loads it" in capsys.readouterr().err


@pytest.mark.parametrize("text, problem", [("", "has no records in it"), ("TN:\n", "has no records in it"),
                                           ("SF:lib/a.js\nend_of_record\n", "the record for lib/a.js has no line data"),
                                           ("SF:lib/a.js\nDA:1,1\nend_of_record\n", "the record for lib/a.js has no line data"),
                                           ("SF:lib/a.js\nLF:0\nLH:0\nend_of_record\n", "the record for lib/a.js has no line data")])
def test_an_empty_report_or_an_incomplete_record_fails_the_gate(tmp_path, monkeypatch, capsys, text, problem):
    root = plugin(tmp_path, **{"lib/a.js": "x();\n"})
    (tmp_path / "lcov.info").write_text(text)
    assert check(tmp_path, monkeypatch, "--lcov", "lcov.info", "--js-root", str(root), "--js-source", "lib/*.js") == 2
    assert problem in capsys.readouterr().err


@pytest.mark.parametrize("argv, message", [([], "give --python, --lcov or both"),
                                           (["--python", "missing.json"], "isn't a coverage JSON report"),
                                           (["--lcov", "missing.info"], "can't read missing.info")])
def test_a_missing_report_fails_the_gate_and_says_why(tmp_path, monkeypatch, capsys, argv, message):
    monkeypatch.chdir(tmp_path)
    if not argv:
        with pytest.raises(SystemExit) as exited:
            gate.main(argv)
        assert exited.value.code == 2
    else:
        assert gate.main(argv) == 2
    assert message in capsys.readouterr().err


def test_the_default_inventories_are_nurus_sources_and_the_shipped_plugin(monkeypatch):
    assert gate.PYTHON_ROOTS == ["nuru/src", "packages/claude-access/src", "servers/deep-think/src",
                                 "servers/laya/src", "servers/obsidian/src"]
    repo = _PATH.parents[1]
    shipped = {p.relative_to(repo / "plugins/obsidian").as_posix() for pattern in gate.JS_SOURCES
               for p in (repo / "plugins/obsidian").glob(pattern)}
    assert {"main.js", "build.js", "install.js", "lib/protocol.js"} <= shipped and "bench/follow.js" not in shipped


def test_it_runs_as_a_script_and_exits_with_its_verdict(tmp_path, monkeypatch, capsys):
    import runpy
    report = python_report(tmp_path, short=(10, 9))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["coverage_gate.py", "--python", str(report), "--python-root", "src"])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(_PATH), run_name="__main__")
    assert exited.value.code == 1 and "short.py: 9/10 lines" in capsys.readouterr().out
