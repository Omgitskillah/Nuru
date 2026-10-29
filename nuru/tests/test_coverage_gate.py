"""F13's coverage gate (tools/coverage_gate.py) on real report formats."""
import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "tools" / "coverage_gate.py"
_spec = importlib.util.spec_from_file_location("coverage_gate", _PATH)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def python_report(tmp_path, **files):
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"files": {name.replace("__", "/"): {"summary": {"num_statements": n, "covered_lines": c}}
                                            for name, (n, c) in files.items()}}))
    return report


def plugin(tmp_path, **files):
    """Plugin sources under a JS root: name -> (code lines, comment and blank lines, a multiple of 5)."""
    root = tmp_path / "plugin"
    for name, (code, other) in files.items():
        path = root / name.replace("__", "/").replace("_js", ".js")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// header\n/* block\n * more\n */\n\n" * (other // 5) + "x();\n" * code, encoding="utf-8")
    return root


def test_small_modules_need_every_line_and_large_ones_more_than_90_percent(tmp_path, capsys):
    report = python_report(tmp_path, nuru__small_full=(150, 150), nuru__small_short=(150, 149),
                           nuru__large_ok=(1000, 901), nuru__large_at_90=(1000, 900), nuru__empty=(0, 0))
    assert gate.main(["--python", str(report)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "nuru/large_at_90: 900/1000 lines, 90.0%; needs above 90%",
        "nuru/small_short: 149/150 lines, 99.3%; needs 100% (small)",
        "3 of 5 modules meet the coverage rule"]


def test_js_files_are_measured_by_code_lines_and_test_files_are_left_out(tmp_path, capsys):
    report = python_report(tmp_path, **{"nuru\\ok": (10, 10)})  # a Windows path, normalized
    root = plugin(tmp_path, lib__client_js=(125, 50), lib__view_js=(1000, 0))
    lcov = tmp_path / "lcov.info"
    # client: 175 lines to Node, 125 of them code, 9 missed; view: 1000 lines, 101 missed.
    lcov.write_text("TN:\nSF:lib\\client.js\nDA:1,1\nLF:175\nLH:166\nend_of_record\n"
                    "SF:test/client.test.js\nLF:10\nLH:1\nend_of_record\n"
                    "SF:node_modules/x/index.js\nLF:10\nLH:1\nend_of_record\n"
                    "SF:lib/view.js\nLF:1000\nLH:899\nend_of_record\n")
    assert gate.main(["--python", str(report), "--lcov", str(lcov), "--js-root", str(root)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "lib/client.js: 116/125 lines, 92.8%; needs 100% (small)",  # small by its code, not Node's 175 lines
        "lib/view.js: 899/1000 lines, 89.9%; needs above 90%",
        "1 of 3 modules meet the coverage rule"]


def test_comment_and_blank_lines_are_not_code(tmp_path):
    root = plugin(tmp_path, lib__a_js=(100, 200))
    assert gate.code_lines(root / "lib" / "a.js") == 100


def test_everything_meeting_the_rule_passes(tmp_path, capsys):
    root = plugin(tmp_path, lib__a_js=(20, 10))
    lcov = tmp_path / "lcov.info"
    lcov.write_text("SF:lib/a.js\nLF:30\nLH:30\nend_of_record\n")
    assert gate.main(["--lcov", str(lcov), "--js-root", str(root)]) == 0
    assert capsys.readouterr().out == "1 of 1 modules meet the coverage rule\n"


@pytest.mark.parametrize("argv, message", [([], "give --python, --lcov or both"),
                                           (["--python", "missing.json"], "a test job didn't produce it"),
                                           (["--lcov", "missing.info"], "a test job didn't produce it")])
def test_a_missing_report_fails_the_gate_and_says_why(tmp_path, monkeypatch, capsys, argv, message):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exited:
        gate.main(argv)
    assert exited.value.code == 2 and message in capsys.readouterr().err


def test_it_runs_as_a_script_and_exits_with_its_verdict(tmp_path, monkeypatch, capsys):
    import runpy
    report = python_report(tmp_path, short=(10, 9))
    monkeypatch.setattr("sys.argv", ["coverage_gate.py", "--python", str(report)])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(_PATH), run_name="__main__")
    assert exited.value.code == 1 and "short: 9/10 lines" in capsys.readouterr().out
