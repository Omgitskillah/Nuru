"""F13's coverage rule, per module: above 90% of its executable lines, and every one of them in a small
module (at most SMALL). Both systems' runs count together, so each line counts on the one where it can run.

Python comes from a coverage JSON report (`coverage json`, after `coverage combine` of the Ubuntu and
Windows data); every .py file under --python-root must be in it. The Obsidian plugin comes from Node's
lcov reports, one per system. Node marks blank and comment lines inside code that ran as run, so a
file's executable lines are its code lines, from tools/js_code_lines.js (esbuild's parse), and one is
covered when any report ran it; every --js-source must have a record in some report, so a module no
test loads fails rather than vanishing.

    python tools/coverage_gate.py --python coverage.json [--python-root nuru/src ...]
                                  --lcov ubuntu/lcov.info --lcov windows/lcov.info
                                  --js-code-lines code-lines.json --js-root plugins/obsidian

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


def python_modules(report: Path, roots: list[Path]) -> dict[str, tuple[int, int]]:
    try:
        files = json.loads(report.read_text(encoding="utf-8"))["files"]
        modules = {name.replace("\\", "/"): (info["summary"]["num_statements"], info["summary"]["covered_lines"])
                   for name, info in files.items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise ReportError(f"{report} isn't a coverage JSON report: {error}") from None
    if not modules:
        raise ReportError(f"{report} has no files in it")
    for name, (statements, covered) in modules.items():
        if not (type(statements) is int and type(covered) is int and 0 <= covered <= statements):
            raise ReportError(f"{report}: {name} has impossible counts ({covered} of {statements} statements)")
    missing = sorted(path.as_posix() for root in roots for path in root.rglob("*.py") if path.as_posix() not in modules)
    if missing:
        raise ReportError(f"{report} leaves out {len(missing)} source file(s), e.g. {missing[0]}: "
                          "was coverage's `source` setting used?")
    return modules


def lcov_hits(report: Path) -> dict[str, set[int]]:
    """{source file: the lines it ran}, from one lcov report. Every record must be complete and agree
    with itself: LF lines found, LH of them hit, one DA per line, ended by end_of_record."""
    try:
        text = report.read_text(encoding="utf-8")
    except OSError as error:
        raise ReportError(f"can't read {report}: {error}") from None
    hits: dict[str, set[int]] = {}
    name = None
    for line in text.splitlines():
        if line.startswith("SF:"):
            if name is not None:
                raise ReportError(f"{report}: the record for {name} has no end_of_record")
            name, counts, found, hit = line[3:].replace("\\", "/"), {}, None, None
        elif name is None:
            continue
        elif line.startswith("DA:"):
            try:
                number, count = (int(part) for part in line[3:].split(",")[:2])
            except ValueError:
                raise ReportError(f"{report}: {name} has a malformed line record {line!r}") from None
            if number < 1 or count < 0 or number in counts:
                raise ReportError(f"{report}: {name} has an impossible line record {line!r}")
            counts[number] = count
        elif line.startswith(("LF:", "LH:")):
            try:
                value = int(line[3:])
            except ValueError:
                raise ReportError(f"{report}: {name} has a malformed {line[:2]} {line!r}") from None
            found, hit = (value, hit) if line.startswith("LF:") else (found, value)
        elif line == "end_of_record":
            ran = {number for number, count in counts.items() if count}
            if not counts or found != len(counts) or hit != len(ran):
                raise ReportError(f"{report}: the record for {name} doesn't add up "
                                  f"({len(counts)} lines, {len(ran)} run; LF {found}, LH {hit})")
            hits.setdefault(name, set()).update(ran)
            name = None
    if name is not None:
        raise ReportError(f"{report}: the record for {name} has no end_of_record")
    if not hits:
        raise ReportError(f"{report} has no records in it")
    return hits


def code_lines(listing: Path) -> dict[str, list[int]]:
    """tools/js_code_lines.js's output: {source: its code lines}."""
    try:
        lines = json.loads(listing.read_text(encoding="utf-8"))
        if not isinstance(lines, dict) or not all(isinstance(v, list) and all(type(n) is int and n > 0 for n in v)
                                                  for v in lines.values()):
            raise ValueError("expected {source: [line numbers]}")
    except (OSError, ValueError) as error:
        raise ReportError(f"{listing} isn't a code-line listing: {error}") from None
    return lines


def js_modules(reports: list[Path], listing: Path, root: Path, patterns: list[str]) -> dict[str, tuple[int, int]]:
    code_by_file = code_lines(listing)
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
            if name not in code_by_file:
                raise ReportError(f"{listing} has no code lines for {name}")
            code = code_by_file[name]
            modules[name] = (len(code), sum(1 for number in code if number in run))
    return modules


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", type=Path)
    parser.add_argument("--python-root", type=Path, action="append", help="default: Nuru's source roots")
    parser.add_argument("--lcov", type=Path, action="append", help="one per system")
    parser.add_argument("--js-code-lines", type=Path, help="tools/js_code_lines.js's output, needed with --lcov")
    parser.add_argument("--js-root", type=Path, default=Path("plugins/obsidian"), help="what lcov's paths are relative to")
    parser.add_argument("--js-source", action="append", help=f"glob under --js-root (default: {JS_SOURCES})")
    args = parser.parse_args(argv)
    if not args.python and not args.lcov:
        parser.error("give --python, --lcov or both")
    if args.lcov and not args.js_code_lines:
        parser.error("--lcov needs --js-code-lines")
    try:
        modules = {**(python_modules(args.python, args.python_root or [Path(r) for r in PYTHON_ROOTS]) if args.python else {}),
                   **(js_modules(args.lcov, args.js_code_lines, args.js_root, args.js_source or JS_SOURCES) if args.lcov else {})}
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
