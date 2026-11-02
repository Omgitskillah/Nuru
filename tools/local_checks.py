"""What CI no longer runs, on this machine: every test suite, then F13's coverage gate over this run's
coverage and the Windows run's, for the commit checked out.

    uv run --locked python tools/local_checks.py tests    # before pushing: all suites, coverage kept
    uv run --locked python tools/local_checks.py gate     # once the pushed commit's Tests run is done

`tests` saves its coverage under .runs/local-checks/<commit>/. `gate` adds the Windows coverage of the
Tests run for that commit (fetched with gh, or --windows-coverage FILE) and runs tools/coverage_gate.py,
so a line counts on the system where it can run. Exits as the failing step does.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "obsidian"
SOURCES = ["nuru/src", "packages/claude-access/src", "servers/deep-think/src", "servers/laya/src",
           "servers/obsidian/src", "tools"]
TESTS = ["nuru/tests", "packages/claude-access/tests", "servers/deep-think/tests", "servers/laya/tests",
         "servers/obsidian/tests", "servers/obsidian/vendor/access-logging/tests"]


class Failed(Exception):
    def __init__(self, code):
        self.code = code


def run(*command, cwd=None, env=None, stdout=None):
    print("+", " ".join(map(str, command)), flush=True)
    code = subprocess.run(list(command), cwd=cwd or ROOT, env=env, stdout=stdout).returncode
    if code:
        raise Failed(code)


def commit() -> str:
    """The commit checked out; a run of uncommitted changes would be evidence for no commit."""
    git = lambda *args: subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    if git("status", "--porcelain", "--untracked-files=no").strip():
        print("uncommitted changes: commit them first, so the checks are for a commit", file=sys.stderr)
        raise Failed(2)
    return git("rev-parse", "HEAD").strip()


def saved(sha: str) -> Path:
    return ROOT / ".runs" / "local-checks" / sha


def tests(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as vault:
        # As CI ran them: an empty vault, no semantic index, no steward.
        env = os.environ | {"VAULT_PATH": vault, "OBSIDIAN_SEMANTIC_DB": "", "NURU_STEWARD_ENABLED": "0",
                            "COVERAGE_FILE": str(out / ".coverage")}
        run("uv", "run", "--locked", "pytest", "-q", "-o", "faulthandler_timeout=180", *TESTS,
            *[f"--cov={s}" for s in SOURCES], "--cov-report=", env=env)
    run("npm", "ci", cwd=PLUGIN)
    run("npm", "run", "test:coverage", cwd=PLUGIN)
    shutil.copy(PLUGIN / "lcov.info", out / "lcov.info")
    with open(out / "code-lines.json", "w", encoding="utf-8") as lines:
        run("node", "../../tools/js_code_lines.js", cwd=PLUGIN, stdout=lines)
    run("npm", "run", "test:e2e", cwd=PLUGIN)
    run("npm", "run", "build", cwd=PLUGIN)


def windows_coverage(sha: str, into: Path) -> Path:
    """The Python coverage the Tests workflow saved for this commit (a pull request's run or main's)."""
    listed = subprocess.run(["gh", "run", "list", "--workflow", "tests.yml", "--commit", sha, "--status", "success",
                             "--json", "databaseId", "--jq", ".[0].databaseId"],
                            cwd=ROOT, capture_output=True, text=True)
    run_id = listed.stdout.strip()
    if listed.returncode or not run_id:
        print(f"no successful Tests run for {sha[:7]} yet: push it, wait for CI, or pass --windows-coverage",
              file=sys.stderr)
        raise Failed(2)
    run("gh", "run", "download", run_id, "-n", "coverage-windows-latest", "-D", str(into))
    return into / ".coverage"


def gate(out: Path, windows: Path) -> None:
    if not (out / ".coverage").is_file():
        print(f"no local run saved in {out}: run `tests` first", file=sys.stderr)
        raise Failed(2)
    env = os.environ | {"COVERAGE_FILE": str(out / "combined.coverage")}
    run("uv", "run", "--locked", "coverage", "combine", "--keep", str(out / ".coverage"), str(windows), env=env)
    run("uv", "run", "--locked", "coverage", "json", "-o", str(out / "coverage.json"), env=env)
    run("uv", "run", "--locked", "python", "tools/coverage_gate.py", "--python", str(out / "coverage.json"),
        "--js-root", "plugins/obsidian", "--lcov", str(out / "lcov.info"),
        "--js-code-lines", str(out / "code-lines.json"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("step", choices=["tests", "gate"])
    parser.add_argument("--windows-coverage", type=Path, help="the Windows run's .coverage, instead of fetching it")
    args = parser.parse_args(argv)
    try:
        sha = commit()
        if args.step == "tests":
            tests(saved(sha))
        else:
            with tempfile.TemporaryDirectory() as fetched:
                gate(saved(sha), args.windows_coverage or windows_coverage(sha, Path(fetched)))
    except Failed as failed:
        return failed.code
    print(f"{args.step}: passed for {sha[:7]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
