"""What CI runs on Windows, and the checks that keep it whole.

CI runs only what needs Windows: the pytest arguments in tools/windows_tests.txt (one per line, a test
file or a test's node id) and the plugin's `npm run test:windows` files. A test needs Windows when:

- it names the platform itself (sys.platform, os.name, ...), in its body, its decorators or its file's
  pytestmark; for the plugin, a test file that names the platform or starts processes;
- or no other selected test runs a line of the sources that only Windows runs: each such line must
  run in some selected test, so the Windows run's coverage, added to a local run's, misses nothing
  (tools/local_checks.py gate). Finding those lines takes a whole-suite Windows run that
  records which test ran each line (the Tests workflow, run by hand with `full: windows-latest`, which
  traces with COVERAGE_CORE=ctrace: the default tracer records only the first test to run a line), set
  against a whole local run:

    python tools/windows_selection.py --windows full-windows.coverage --local .coverage

A line run outside any test, as a module is imported, counts for any selected test that imports it.

The plugin's selection is behaviour, not lines: no plugin line runs only on Windows (its platform
checks mostly take the platform as a parameter, so tests run each branch anywhere; lib/install.js's
uv.exe choice is one line either way, which line coverage can't tell apart), but its paths, process starts, installs and the end
to end run against nuru-serve behave as Windows makes them only there. Hence the files that name the
platform or start processes, and those that write the install's files: build, envfiles, install,
install-script, launcher, ollama, remove, settings, setup, unpack and e2e/scenarios.

What this can't see, and so when to refresh the coverage comparison (a `full: windows-latest` run,
then this command against a whole local run on that commit):
- the regex reads test bodies, decorators and pytestmark, not fixtures, helpers or conftest.py: a test
  that depends on the platform only through one of those is found only by the coverage comparison;
- that comparison is of the code as it was: a later change can add a Windows-only branch, or a
  Windows-only import-time line, that no selected test runs. Refresh it in any change that adds or
  moves code behind a platform check (os.name, sys.platform, msvcrt, ctypes.windll, a POSIX-only
  module's absence) or a fixture or helper that branches on the platform, and before each release;
- the coverage gate (tools/local_checks.py gate) is the backstop between refreshes: a Windows-only
  line no selected test runs counts as missed, and fails a module that it takes below the rule.

nuru/tests/test_windows_selection.py checks the first kind on every local run. This command checks both:
it prints each test that names the platform and isn't selected, each Windows-only line no selected
test runs (with a test that does), and each entry that names no test file, and exits 1 if there are any.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

from coverage import CoverageData

ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / "tools" / "windows_tests.txt"
PYTHON_TESTS = ["nuru/tests", "packages/claude-access/tests", "servers/deep-think/tests", "servers/laya/tests",
                "servers/obsidian/tests", "servers/obsidian/vendor/access-logging/tests"]
PLUGIN = ROOT / "plugins" / "obsidian"
PLATFORM = re.compile(r"sys\.platform|os\.name\b|platform\.system|_WINDOWS\b|IS_WINDOWS\b|win32|[\"']nt[\"']")
# This tool's own tests quote the patterns in the test files they write.
NOT_PLATFORM = {"nuru/tests/test_windows_selection.py"}
PLUGIN_PLATFORM = re.compile(r"process\.platform|win32|child_process")


def entries(selection: Path = SELECTION) -> list[str]:
    return [line.strip() for line in selection.read_text(encoding="utf-8").splitlines() if line.strip()]


def selected(test: str, chosen: list[str]) -> bool:
    """Whether a test (a node id, perhaps with parameters) is one an entry runs."""
    return any(test == entry or test.startswith((entry + "::", entry + "[")) for entry in chosen)


def platform_tests(root: Path = ROOT) -> list[str]:
    """Node ids of the Python tests that name the platform: the whole file when its pytestmark does."""
    found = []
    for folder in PYTHON_TESTS:
        for path in sorted((root / folder).glob("test_*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            name = path.relative_to(root).as_posix()
            if name in NOT_PLATFORM:
                continue
            if any(isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "pytestmark" for t in node.targets)
                   and PLATFORM.search(ast.get_source_segment(source, node)) for node in tree.body):
                found.append(name)
                continue
            for node, prefix in _tests(tree.body, name):
                lines = source.splitlines()[min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1:node.end_lineno]
                if PLATFORM.search("\n".join(lines)):
                    found.append(f"{prefix}::{node.name}")
    return found


def _tests(body, prefix):
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            yield node, prefix
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            yield from _tests(node.body, f"{prefix}::{node.name}")


def plugin_platform_files(plugin: Path = PLUGIN) -> list[str]:
    tests = sorted(plugin.glob("test/*.test.js")) + sorted(plugin.glob("test/e2e/*.test.js"))
    return [p.relative_to(plugin).as_posix() for p in tests if PLUGIN_PLATFORM.search(p.read_text(encoding="utf-8"))]


def plugin_selection(plugin: Path = PLUGIN) -> list[str]:
    script = json.loads((plugin / "package.json").read_text(encoding="utf-8"))["scripts"]["test:windows"]
    return [word for word in script.split() if word.endswith(".test.js")]


def _lines(path: Path) -> CoverageData:
    data = CoverageData(str(path))
    data.read()
    return data


def windows_only_lines(windows: Path, local: Path) -> dict[tuple[str, int], set[str]]:
    """Each line the Windows run ran and the local one didn't, and the tests that ran it. A line run
    outside any test (as a module is imported) has none: any selected test that imports it runs it."""
    win, here = _lines(windows), _lines(local)
    ran_here = {_relative(f): set(here.lines(f) or ()) for f in here.measured_files()}
    lines = {}
    for f in win.measured_files():
        only = set(win.lines(f) or ()) - ran_here.get(_relative(f), set())
        for line, contexts in win.contexts_by_lineno(f).items():
            if line in only:
                lines[(_relative(f), line)] = {c.split("|")[0] for c in contexts if c}
    return lines


def _relative(name: str) -> str:
    name = name.replace("\\", "/")
    root = ROOT.as_posix() + "/"
    return name[len(root):] if name.startswith(root) else name


def problems(chosen: list[str], need: set[str], lines: dict[tuple[str, int], set[str]] = {}, root: Path = ROOT) -> list[str]:
    out = [f"needs Windows, not in tools/windows_tests.txt: {t}" for t in sorted(need) if not selected(t, chosen)]
    out += [f"{f}:{n} runs only on Windows, in no selected test; e.g. {min(tests)}"
            for (f, n), tests in sorted(lines.items()) if tests and not any(selected(t, chosen) for t in tests)]
    out += [f"tools/windows_tests.txt names no test file: {e}" for e in chosen if not (root / e.split("::")[0]).is_file()]
    out += [f"starts processes or names the platform, not in test:windows: {f}"
            for f in plugin_platform_files(root / "plugins" / "obsidian")
            if f not in plugin_selection(root / "plugins" / "obsidian")]
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--windows", type=Path, help="coverage data of a whole-suite Windows run, with test contexts")
    parser.add_argument("--local", type=Path, help="coverage data of a whole local run")
    args = parser.parse_args(argv)
    if bool(args.windows) != bool(args.local):
        parser.error("--windows and --local go together")
    need = set(platform_tests())
    lines = windows_only_lines(args.windows, args.local) if args.windows else {}
    found = problems(entries(), need, lines)
    print("\n".join(found) or f"the Windows selection is whole: {len(need)} tests name the platform, "
                               f"{len(lines)} lines run only on Windows")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
