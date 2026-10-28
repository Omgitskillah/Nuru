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


def test_small_modules_need_every_line_and_large_ones_more_than_90_percent(tmp_path, capsys):
    report = python_report(tmp_path, nuru__small_full=(150, 150), nuru__small_short=(150, 149),
                           nuru__large_ok=(1000, 901), nuru__large_at_90=(1000, 900), nuru__empty=(0, 0))
    assert gate.main(["--python", str(report)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out == ["nuru/large_at_90: 900/1000 lines, 90.0%; needs above 90%",
                   "nuru/small_short: 149/150 lines, 99.3%; needs 100% (small)",
                   "3 of 5 modules meet the coverage rule"]


def test_windows_paths_and_lcov_reports_are_read_and_test_files_are_left_out(tmp_path, capsys):
    report = python_report(tmp_path, ok=(10, 10))
    lcov = tmp_path / "lcov.info"
    lcov.write_text("TN:\nSF:C:\\nuru\\plugins\\obsidian\\lib\\client.js\nDA:1,1\nLF:125\nLH:116\nend_of_record\n"
                    "SF:/nuru/plugins/obsidian/test/client.test.js\nLF:10\nLH:1\nend_of_record\n"
                    "SF:/nuru/plugins/obsidian/node_modules/x/index.js\nLF:10\nLH:1\nend_of_record\n"
                    "SF:/nuru/plugins/obsidian/lib/view.js\nLF:1000\nLH:967\nend_of_record\n")
    assert gate.main(["--python", str(report), "--lcov", str(lcov)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "C:/nuru/plugins/obsidian/lib/client.js: 116/125 lines, 92.8%; needs 100% (small)",
        "2 of 3 modules meet the coverage rule"]


def test_everything_meeting_the_rule_passes(tmp_path, capsys):
    lcov = tmp_path / "lcov.info"
    lcov.write_text("SF:lib/a.js\nLF:20\nLH:20\nend_of_record\n")
    assert gate.main(["--lcov", str(lcov)]) == 0
    assert capsys.readouterr().out == "1 of 1 modules meet the coverage rule\n"


def test_a_report_must_be_given(capsys):
    with pytest.raises(SystemExit):
        gate.main([])
    assert "give --python, --lcov or both" in capsys.readouterr().err


def test_it_runs_as_a_script_and_exits_with_its_verdict(tmp_path, monkeypatch, capsys):
    import runpy
    report = python_report(tmp_path, short=(10, 9))
    monkeypatch.setattr("sys.argv", ["coverage_gate.py", "--python", str(report)])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(_PATH), run_name="__main__")
    assert exited.value.code == 1 and "short: 9/10 lines" in capsys.readouterr().out
