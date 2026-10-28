"""F13's coverage rule, per module: above 90% of its lines, and every line of a small one (at most
SMALL lines). Python from a coverage JSON report (`coverage json`, after combining the Ubuntu and Windows
runs, so each line counts on the platform where it can run); the Obsidian plugin from Node's lcov report.

    python tools/coverage_gate.py [--python coverage.json] [--lcov lcov.info]

Prints each module that falls short and exits 1 if any does.
"""
import argparse
import json
import sys
from pathlib import Path

SMALL = 150
FLOOR = 90.0


def short(statements: int, covered: int) -> bool:
    if statements == 0:
        return False
    if statements <= SMALL:
        return covered < statements
    return covered * 100 / statements <= FLOOR


def python_modules(report: Path) -> dict[str, tuple[int, int]]:
    files = json.loads(report.read_text(encoding="utf-8"))["files"]
    return {name.replace("\\", "/"): (info["summary"]["num_statements"], info["summary"]["covered_lines"])
            for name, info in files.items()}


def lcov_modules(report: Path) -> dict[str, tuple[int, int]]:
    modules, name, found, hit = {}, None, 0, 0
    for line in report.read_text(encoding="utf-8").splitlines():
        if line.startswith("SF:"):
            name, found, hit = line[3:].replace("\\", "/"), 0, 0
        elif line.startswith("LF:"):
            found = int(line[3:])
        elif line.startswith("LH:"):
            hit = int(line[3:])
        elif line == "end_of_record" and name is not None:
            if "/test/" not in name and "/node_modules/" not in name:
                modules[name] = (found, hit)
            name = None
    return modules


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", type=Path)
    parser.add_argument("--lcov", type=Path)
    args = parser.parse_args(argv)
    if not args.python and not args.lcov:
        parser.error("give --python, --lcov or both")
    modules = {**(python_modules(args.python) if args.python else {}), **(lcov_modules(args.lcov) if args.lcov else {})}
    failing = sorted((name, statements, covered) for name, (statements, covered) in modules.items()
                     if short(statements, covered))
    for name, statements, covered in failing:
        need = "100% (small)" if statements <= SMALL else f"above {FLOOR:g}%"
        print(f"{name}: {covered}/{statements} lines, {covered * 100 / statements:.1f}%; needs {need}")
    print(f"{len(modules) - len(failing)} of {len(modules)} modules meet the coverage rule")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
