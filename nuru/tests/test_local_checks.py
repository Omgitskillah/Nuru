"""tools/local_checks.py, the suites and coverage gate CI no longer runs, in a real git checkout written
here, with uv, npm, node and gh stood in for: what it runs, where the evidence goes, and that it refuses
to vouch for uncommitted changes or a commit CI hasn't passed."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "tools" / "local_checks.py"
_spec = importlib.util.spec_from_file_location("local_checks", _PATH)
lc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lc)


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A committed checkout with a plugin folder, as the module's ROOT."""
    git = lambda *args: subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "plugins" / "obsidian").mkdir(parents=True)
    (tmp_path / "README.md").write_text("x\n")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    monkeypatch.setattr(lc, "ROOT", tmp_path)
    monkeypatch.setattr(lc, "PLUGIN", tmp_path / "plugins" / "obsidian")
    return tmp_path


class Tools:
    """uv, npm, node and gh as the script calls them: records each command (and its environment's
    COVERAGE_FILE and VAULT_PATH), writes what the real one would, and fails the one named in `fail`."""

    def __init__(self, root, fail=None, runs="41537739301"):
        self.root, self.fail, self.runs, self.calls = root, fail, runs, []
        self.real = subprocess.run

    def __call__(self, command, cwd=None, env=None, stdout=None, **kwargs):
        if command[0] == "git":
            return self.real(command, cwd=cwd, env=env, stdout=stdout, **kwargs)
        env = env or {}
        self.calls.append((command, Path(cwd), env.get("COVERAGE_FILE"), env.get("VAULT_PATH")))
        if self.fail and self.fail in " ".join(command):
            return subprocess.CompletedProcess(command, 3, "", "")
        if command[:3] == ["gh", "run", "list"]:
            return subprocess.CompletedProcess(command, 0, self.runs + "\n", "")
        if command[:3] == ["gh", "run", "download"]:
            Path(command[command.index("-D") + 1], ".coverage").write_text("windows")
        if command[-1] == "test:coverage":
            (Path(cwd) / "lcov.info").write_text("SF:main.js\n")
        if command[0] == "uv" and "pytest" in command:
            Path(env["COVERAGE_FILE"]).write_text("local")
            assert Path(env["VAULT_PATH"]).is_dir()
        if stdout:
            stdout.write("{}")
        return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture
def tools(checkout, monkeypatch):
    def install(**kwargs):
        fake = Tools(checkout, **kwargs)
        monkeypatch.setattr(lc.subprocess, "run", fake)
        return fake
    return install


def head(root):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()


def test_tests_runs_every_suite_and_keeps_its_evidence_under_the_commit(checkout, tools, capsys):
    fake = tools()
    assert lc.main(["tests"]) == 0
    out = checkout / ".runs" / "local-checks" / head(checkout)
    plugin = checkout / "plugins" / "obsidian"
    [pytest_call, *rest] = fake.calls
    command, cwd, coverage_file, vault = pytest_call
    assert command[:4] == ["uv", "run", "--locked", "pytest"] and set(lc.TESTS) <= set(command)
    assert {f"--cov={s}" for s in lc.SOURCES} <= set(command) and cwd == checkout
    assert coverage_file == str(out / ".coverage") and not Path(vault).exists()  # an empty vault, gone after
    assert [(c, d) for c, d, _, _ in rest] == [
        (["npm", "ci"], plugin), (["npm", "run", "test:coverage"], plugin),
        (["node", "../../tools/js_code_lines.js"], plugin), (["npm", "run", "test:e2e"], plugin),
        (["npm", "run", "build"], plugin)]
    assert sorted(p.name for p in out.iterdir()) == [".coverage", "code-lines.json", "lcov.info"]
    assert capsys.readouterr().out.endswith(f"tests: passed for {head(checkout)[:7]}\n")


def test_a_failing_suite_stops_the_rest_with_its_exit_code(tools):
    fake = tools(fail="test:coverage")
    assert lc.main(["tests"]) == 3
    assert fake.calls[-1][0] == ["npm", "run", "test:coverage"]


def test_uncommitted_changes_are_refused_before_anything_runs(checkout, tools, capsys):
    fake = tools()
    (checkout / "README.md").write_text("changed\n")
    assert lc.main(["tests"]) == 2 and fake.calls == []
    assert "commit them first" in capsys.readouterr().err


def test_an_untracked_file_doesnt_count_as_a_change(checkout, tools):
    tools()
    (checkout / "design_discussions.md").write_text("board\n")
    assert lc.main(["tests"]) == 0


def test_gate_adds_the_commits_windows_coverage_to_the_local_run_and_judges_both(checkout, tools, capsys):
    fake = tools()
    lc.main(["tests"])
    fake.calls.clear()
    assert lc.main(["gate"]) == 0
    sha, out = head(checkout), checkout / ".runs" / "local-checks" / head(checkout)
    [listed, download, combine, report, gate] = [c for c, _, _, _ in fake.calls]
    assert listed[:3] == ["gh", "run", "list"] and listed[listed.index("--commit") + 1] == sha
    assert "success" in listed and download[:4] == ["gh", "run", "download", "41537739301"]
    assert download[download.index("-n") + 1] == "coverage-windows-latest"
    fetched = Path(download[download.index("-D") + 1]) / ".coverage"
    assert combine[-3:] == ["--keep", str(out / ".coverage"), str(fetched)]
    assert fake.calls[2][2] == fake.calls[3][2] == str(out / "combined.coverage")
    assert report[-3:] == ["json", "-o", str(out / "coverage.json")]
    assert gate[4:] == ["tools/coverage_gate.py", "--python", str(out / "coverage.json"), "--js-root", "plugins/obsidian",
                        "--lcov", str(out / "lcov.info"), "--js-code-lines", str(out / "code-lines.json")]


def test_gate_can_take_the_windows_coverage_from_a_file(checkout, tools, tmp_path):
    fake = tools()
    lc.main(["tests"])
    fake.calls.clear()
    assert lc.main(["gate", "--windows-coverage", str(tmp_path / "w.coverage")]) == 0
    assert not any(c[0] == "gh" for c, _, _, _ in fake.calls)
    assert fake.calls[0][0][-1] == str(tmp_path / "w.coverage")


def test_gate_without_a_passed_ci_run_says_what_to_do(tools, capsys):
    tools(runs="")
    lc.main(["tests"])
    assert lc.main(["gate"]) == 2
    assert "no successful Tests run" in capsys.readouterr().err


def test_gate_without_a_local_run_for_this_commit_says_so(tools, capsys):
    tools()
    assert lc.main(["gate"]) == 2
    assert "run `tests` first" in capsys.readouterr().err


def test_a_failing_gate_fails_the_command(tools):
    tools(fail="coverage_gate.py")
    lc.main(["tests"])
    assert lc.main(["gate"]) == 3


def test_run_as_a_command_it_exits_with_its_verdict(monkeypatch, capsys):
    import runpy
    monkeypatch.setattr("sys.argv", ["local_checks.py", "nonsense"])
    with pytest.raises(SystemExit) as exit:
        runpy.run_path(str(_PATH), run_name="__main__")
    assert exit.value.code == 2 and "invalid choice" in capsys.readouterr().err
