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


@pytest.mark.parametrize("statements, covered", [(-1, 0), (10, 11), (10, -1), ("10", 10), (10, True)])
def test_impossible_python_counts_fail_the_gate(tmp_path, monkeypatch, capsys, statements, covered):
    report = python_report(tmp_path, a=(1, 1))
    report.write_text(json.dumps({"files": {"src/a.py": {"summary": {"num_statements": statements, "covered_lines": covered}}}}))
    assert check(tmp_path, monkeypatch, "--python", str(report), "--python-root", "src") == 2
    assert "impossible counts" in capsys.readouterr().err


def test_a_source_file_missing_from_the_python_report_fails_the_gate(tmp_path, monkeypatch, capsys):
    report = python_report(tmp_path, imported=(10, 10))
    (tmp_path / "src" / "never_imported.py").write_text("x = 1\n")
    assert check(tmp_path, monkeypatch, "--python", str(report), "--python-root", "src") == 2
    assert "leaves out 1 source file(s), e.g. src/never_imported.py" in capsys.readouterr().err


# ---------------------------------------------------------------- the plugin's lcov reports
# Node's report for a real run (node --test --experimental-test-coverage --test-reporter=lcov) of
#   1 const slashOrStar = /[/*]/;        5 const note = `one
#   2 function never() {                 6 // inside a template string
#   3   throw new Error("not run");      7 three`;
#   4 }                                  8 module.exports = { slashOrStar, never, note };
# with a test that loads it and never calls never(). js_code_lines.js (esbuild) gives code lines 1-5 and 8.
REAL_NODE = ("SF:lib/a.js\nFN:2,never\nFNDA:0,never\nFNF:1\nFNH:0\nBRDA:1,0,0,1\nBRF:1\nBRH:1\n"
             "DA:1,1\nDA:2,0\nDA:3,0\nDA:4,0\nDA:5,1\nDA:6,1\nDA:7,1\nDA:8,1\nLH:5\nLF:8\nend_of_record\n")
REAL_CODE = {"lib/a.js": [1, 2, 3, 4, 5, 8]}


def plugin(tmp_path, code=REAL_CODE, **files):
    root = tmp_path / "plugin"
    for name in {**{n: "" for n in code}, **files}:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("x();\n")
    listing = tmp_path / "code-lines.json"
    listing.write_text(json.dumps(code))
    return root, listing


def lcov(path, records):
    """An lcov report as Node writes it: {source: {line: hits}}."""
    lines = []
    for name, hits in records.items():
        lines += [f"SF:{name}", *(f"DA:{n},{h}" for n, h in sorted(hits.items())),
                  f"LH:{sum(1 for h in hits.values() if h)}", f"LF:{len(hits)}", "end_of_record"]
    path.write_text("\n".join(lines) + "\n")
    return path


def js(tmp_path, monkeypatch, reports, listing, root, *extra):
    return check(tmp_path, monkeypatch, *[arg for r in reports for arg in ("--lcov", str(r))],
                 "--js-code-lines", str(listing), "--js-root", str(root), "--js-source", "lib/*.js", *extra)


def test_an_uncalled_function_below_a_regex_counts_as_missed_in_a_real_node_report(tmp_path, monkeypatch, capsys):
    """Node marks the comment-only and string lines run; only code lines count, and never() didn't run."""
    root, listing = plugin(tmp_path)
    (tmp_path / "lcov.info").write_text(REAL_NODE)
    assert js(tmp_path, monkeypatch, [tmp_path / "lcov.info"], listing, root) == 1
    assert capsys.readouterr().out.splitlines()[0] == "lib/a.js: 3/6 lines, 50.0%; needs 100% (small)"


def test_a_line_run_on_either_system_counts(tmp_path, monkeypatch, capsys):
    root, listing = plugin(tmp_path, code={"lib/a.js": [1, 2, 3]})
    ubuntu = lcov(tmp_path / "ubuntu.info", {"lib/a.js": {1: 1, 2: 1, 3: 0}})  # line 3 only runs on Windows
    # Another system's checkout: an absolute Windows path to the same file.
    windows = lcov(tmp_path / "windows.info", {"D:\\a\\Nuru\\plugin\\lib\\a.js": {1: 1, 2: 0, 3: 1}})
    assert js(tmp_path, monkeypatch, [ubuntu], listing, root) == 1
    assert capsys.readouterr().out.splitlines()[0] == "lib/a.js: 2/3 lines, 66.7%; needs 100% (small)"
    assert js(tmp_path, monkeypatch, [ubuntu, windows], listing, root) == 0
    assert capsys.readouterr().out == "1 of 1 modules meet the coverage rule\n"


def test_tests_and_files_outside_the_sources_are_ignored(tmp_path, monkeypatch, capsys):
    root, listing = plugin(tmp_path, code={"lib/a.js": [1], "test/a.test.js": [1]})
    report = lcov(tmp_path / "lcov.info", {"lib/a.js": {1: 1}, "test/a.test.js": {1: 0}, "node_modules/x/i.js": {1: 0}})
    assert js(tmp_path, monkeypatch, [report], listing, root) == 0
    assert capsys.readouterr().out == "1 of 1 modules meet the coverage rule\n"


def test_a_source_no_test_loads_fails_the_gate(tmp_path, monkeypatch, capsys):
    root, listing = plugin(tmp_path, code={"lib/a.js": [1], "lib/unloaded.js": [1]})
    report = lcov(tmp_path / "lcov.info", {"lib/a.js": {1: 1}})
    assert js(tmp_path, monkeypatch, [report], listing, root) == 2
    assert "no lcov report has lib/unloaded.js: no test loads it" in capsys.readouterr().err


def test_a_source_missing_from_the_code_line_listing_fails_the_gate(tmp_path, monkeypatch, capsys):
    root, listing = plugin(tmp_path, code={"lib/a.js": [1]}, **{"lib/new.js": ""})
    report = lcov(tmp_path / "lcov.info", {"lib/a.js": {1: 1}, "lib/new.js": {1: 1}})
    assert js(tmp_path, monkeypatch, [report], listing, root) == 2
    assert "has no code lines for lib/new.js" in capsys.readouterr().err


@pytest.mark.parametrize("listing, problem", [("{not json", "isn't a code-line listing"), ('["lib/a.js"]', "isn't a code-line listing"),
                                              ('{"lib/a.js": [0]}', "isn't a code-line listing"),
                                              ('{"lib/a.js": ["1"]}', "isn't a code-line listing")])
def test_a_malformed_code_line_listing_fails_the_gate(tmp_path, monkeypatch, capsys, listing, problem):
    root, path = plugin(tmp_path)
    path.write_text(listing)
    (tmp_path / "lcov.info").write_text(REAL_NODE)
    assert js(tmp_path, monkeypatch, [tmp_path / "lcov.info"], path, root) == 2
    assert problem in capsys.readouterr().err


GOOD = "SF:lib/a.js\nDA:1,1\nLH:1\nLF:1\nend_of_record\n"


@pytest.mark.parametrize("text, problem", [
    ("", "has no records in it"), ("TN:\n", "has no records in it"),
    ("SF:lib/a.js\nend_of_record\n", "doesn't add up"),
    ("SF:lib/a.js\nDA:1,1\nend_of_record\n", "doesn't add up"),  # no LF or LH
    ("SF:lib/a.js\nDA:1,1\nLF:999\nLH:0\nend_of_record\n", "doesn't add up"),
    ("SF:lib/a.js\nDA:1,1\nLF:1\nLH:0\nend_of_record\n", "doesn't add up"),
    (GOOD + "SF:lib/b.js\nDA:1,0\n", "the record for lib/b.js has no end_of_record"),
    ("SF:lib/a.js\nDA:1,1\nSF:lib/b.js\nDA:1,1\nLF:1\nLH:1\nend_of_record\n", "the record for lib/a.js has no end_of_record"),
    ("SF:lib/a.js\nDA:one,1\nLF:1\nLH:1\nend_of_record\n", "malformed line record"),
    ("SF:lib/a.js\nDA:1,-1\nLF:1\nLH:0\nend_of_record\n", "impossible line record"),
    ("SF:lib/a.js\nDA:1,1\nDA:1,1\nLF:1\nLH:1\nend_of_record\n", "impossible line record"),
    ("SF:lib/a.js\nDA:1,1\nLF:x\nLH:1\nend_of_record\n", "malformed LF"),
])
def test_an_empty_report_or_an_incomplete_or_inconsistent_record_fails_the_gate(tmp_path, monkeypatch, capsys, text, problem):
    root, listing = plugin(tmp_path, code={"lib/a.js": [1], "lib/b.js": [1]})
    (tmp_path / "lcov.info").write_text(text)
    assert js(tmp_path, monkeypatch, [tmp_path / "lcov.info"], listing, root) == 2
    assert problem in capsys.readouterr().err


def test_lcov_needs_its_code_line_listing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exited:
        gate.main(["--lcov", "lcov.info"])
    assert exited.value.code == 2 and "--lcov needs --js-code-lines" in capsys.readouterr().err


@pytest.mark.parametrize("argv, message", [([], "give --python, --lcov or both"),
                                           (["--python", "missing.json"], "isn't a coverage JSON report"),
                                           (["--lcov", "missing.info", "--js-code-lines", "listed.json"], "can't read missing.info"),
                                           (["--lcov", "missing.info", "--js-code-lines", "missing.json"], "missing.json isn't a code-line listing")])
def test_a_missing_report_fails_the_gate_and_says_why(tmp_path, monkeypatch, capsys, argv, message):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "listed.json").write_text("{}")
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
