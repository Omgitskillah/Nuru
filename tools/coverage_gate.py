"""F13's coverage rule, per module: above 90% of its executable lines, and every one of them in a small
module (at most SMALL). Both systems' runs count together, so each line counts on the one where it can run.

Python comes from a coverage JSON report (`coverage json`, after `coverage combine` of the Ubuntu and
Windows data); every .py file under --python-root must be in it. The Obsidian plugin comes from Node's
lcov reports, one per system. Node marks blank and comment lines as run, so a file's executable lines
are its code lines (code_lines), and one is covered when any report ran it; every --js-source must have
a record in some report, so a module no test loads fails rather than vanishing.

    python tools/coverage_gate.py --python coverage.json [--python-root nuru/src ...]
                                  --lcov ubuntu/lcov.info --lcov windows/lcov.info --js-root plugins/obsidian

Prints each module that falls short and exits 1 if any does; 2 for a missing or malformed report.
"""
import argparse
import json
import sys
from pathlib import Path

SMALL = 150
FLOOR = 90.0
PYTHON_ROOTS = ["nuru/src", "packages/claude-access/src", "servers/deep-think/src", "servers/laya/src",
                "servers/obsidian/src"]
# What the plugin ships (main.js and lib/, bundled by build.js) and installs with; not its benchmarks.
JS_SOURCES = ["main.js", "build.js", "install.js", "lib/*.js"]


class ReportError(ValueError):
    """A coverage report that is missing, empty or malformed: the gate can't judge from it."""


def short(statements: int, covered: int) -> bool:
    if statements == 0:
        return False
    if statements <= SMALL:
        return covered < statements
    return covered * 100 / statements <= FLOOR


def code_lines(text: str) -> list[int]:
    """The 1-based numbers of the lines holding code: not blank, and not only comment. Strings are
    read so a `//` or `/*` inside one isn't taken for a comment."""
    lines, in_block = [], False
    for number, line in enumerate(text.splitlines(), 1):
        code, i, quote = False, 0, None
        while i < len(line):
            two = line[i:i + 2]
            if in_block:
                if two == "*/":
                    in_block, i = False, i + 2
                    continue
                i += 1
            elif quote:
                if line[i] == "\\":
                    i += 2
                    continue
                if line[i] == quote:
                    quote = None
                i += 1
            elif two == "//":
                break
            elif two == "/*":
                in_block, i = True, i + 2
            else:
                if line[i] in "'\"`":
                    quote = line[i]
                code = code or not line[i].isspace()
                i += 1
        if code:
            lines.append(number)
    return lines


def python_modules(report: Path, roots: list[Path]) -> dict[str, tuple[int, int]]:
    try:
        files = json.loads(report.read_text(encoding="utf-8"))["files"]
        modules = {name.replace("\\", "/"): (int(info["summary"]["num_statements"]), int(info["summary"]["covered_lines"]))
                   for name, info in files.items()}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ReportError(f"{report} isn't a coverage JSON report: {error}") from None
    if not modules:
        raise ReportError(f"{report} has no files in it")
    missing = sorted(path.as_posix() for root in roots for path in root.rglob("*.py") if path.as_posix() not in modules)
    if missing:
        raise ReportError(f"{report} leaves out {len(missing)} source file(s), e.g. {missing[0]}: "
                          "was coverage's `source` setting used?")
    return modules


def lcov_hits(report: Path) -> dict[str, set[int]]:
    """{source file: the lines it ran}, from one lcov report. Each record must be complete."""
    try:
        text = report.read_text(encoding="utf-8")
    except OSError as error:
        raise ReportError(f"can't read {report}: {error}") from None
    hits: dict[str, set[int]] = {}
    name, seen, has_lf = None, set(), False
    for line in text.splitlines():
        if line.startswith("SF:"):
            name, seen, has_lf = line[3:].replace("\\", "/"), set(), False
        elif line.startswith("DA:") and name is not None:
            number, count = (int(part) for part in line[3:].split(",")[:2])
            if count > 0:
                seen.add(number)
            hits.setdefault(name, set())
        elif line.startswith("LF:") and name is not None:
            has_lf = True
        elif line == "end_of_record" and name is not None:
            if not has_lf or name not in hits:
                raise ReportError(f"{report}: the record for {name} has no line data")
            hits[name] |= seen
            name = None
    if not hits:
        raise ReportError(f"{report} has no records in it")
    return hits


def js_modules(reports: list[Path], root: Path, patterns: list[str]) -> dict[str, tuple[int, int]]:
    ran: dict[str, set[int]] = {}
    for report in reports:
        for name, lines in lcov_hits(report).items():
            ran.setdefault(name, set()).update(lines)
    modules = {}
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            name = path.relative_to(root).as_posix()
            # Relative to the plugin folder, or absolute from another system's checkout.
            lines = [hit for key, hit in ran.items() if key == name or key.endswith(f"/{root.resolve().name}/{name}")]
            if not lines:
                raise ReportError(f"no lcov report has {name}: no test loads it")
            run = set().union(*lines)
            code = code_lines(path.read_text(encoding="utf-8"))
            modules[name] = (len(code), sum(1 for number in code if number in run))
    return modules


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", type=Path)
    parser.add_argument("--python-root", type=Path, action="append", help="default: Nuru's source roots")
    parser.add_argument("--lcov", type=Path, action="append", help="one per system")
    parser.add_argument("--js-root", type=Path, default=Path("plugins/obsidian"), help="what lcov's paths are relative to")
    parser.add_argument("--js-source", action="append", help=f"glob under --js-root (default: {JS_SOURCES})")
    args = parser.parse_args(argv)
    if not args.python and not args.lcov:
        parser.error("give --python, --lcov or both")
    try:
        modules = {**(python_modules(args.python, args.python_root or [Path(r) for r in PYTHON_ROOTS]) if args.python else {}),
                   **(js_modules(args.lcov, args.js_root, args.js_source or JS_SOURCES) if args.lcov else {})}
    except ReportError as error:
        print(f"coverage gate: {error}", file=sys.stderr)
        return 2
    failing = sorted((name, statements, covered) for name, (statements, covered) in modules.items()
                     if short(statements, covered))
    for name, statements, covered in failing:
        need = "100% (small)" if statements <= SMALL else f"above {FLOOR:g}%"
        print(f"{name}: {covered}/{statements} lines, {covered * 100 / statements:.1f}%; needs {need}")
    print(f"{len(modules) - len(failing)} of {len(modules)} modules meet the coverage rule")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
