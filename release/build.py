"""Builds Nuru's release bundle, nuru-<version>.zip: this commit's files, and each server repository's at
the commit this one pins, under one nuru-<version>/ folder. Then SHA256SUMS for every file in the out
folder, which the release workflow has already put the plugin's main.js, manifest.json and styles.css in.

    python release/build.py <version> <out folder>

The version is the release tag, and the plugin's manifest.json and package.json and nuru/pyproject.toml
must all say it. Everything is read from commits, never the working tree, so what is released is
exactly what was tagged."""
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True).stdout


def versions(root: Path) -> dict[str, str]:
    def show(path):
        return git(root, "show", f"HEAD:{path}").decode("utf-8")
    return {"plugins/obsidian/manifest.json": json.loads(show("plugins/obsidian/manifest.json"))["version"],
            "plugins/obsidian/package.json": json.loads(show("plugins/obsidian/package.json"))["version"],
            "nuru/pyproject.toml": tomllib.loads(show("nuru/pyproject.toml"))["project"]["version"]}


def files(root: Path, commit: str, prefix: str):
    """(path, executable, content) for each file of `commit` in the repository at `root`."""
    with tarfile.open(fileobj=io.BytesIO(git(root, "archive", "--format=tar", commit))) as archive:
        for member in archive:
            if member.isfile():
                yield prefix + member.name, bool(member.mode & 0o100), archive.extractfile(member).read()


def bundle(root: Path, version: str, out: Path) -> Path:
    head = git(root, "rev-parse", "HEAD").decode().strip()
    entries = list(files(root, head, ""))
    for line in git(root, "ls-tree", "-r", "-z", head).split(b"\0"):
        if line.startswith(b"160000 "):  # a server repository, at the commit pinned here
            meta, path = line.decode("utf-8").split("\t", 1)
            entries += files(root / path, meta.split()[2], f"{path}/")
    when = datetime.fromtimestamp(int(git(root, "log", "-1", "--format=%ct", head)), timezone.utc)
    target = out / f"nuru-{version}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, executable, content in sorted(entries):
            info = zipfile.ZipInfo(f"nuru-{version}/{path}", when.timetuple()[:6])
            info.external_attr = (0o100755 if executable else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return target


def checksums(out: Path) -> Path:
    sums = out / "SHA256SUMS"
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
             for p in sorted(out.iterdir()) if p.is_file() and p != sums]
    sums.write_text("".join(lines), encoding="utf-8", newline="\n")
    return sums


def main(argv: list[str], root: Path = ROOT) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    version, out = argv[0], Path(argv[1])
    wrong = {path: found for path, found in versions(root).items() if found != version}
    if wrong:
        print(f"Releasing {version}, but " + ", ".join(f"{p} says {v}" for p, v in wrong.items()), file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    print(bundle(root, version, out))
    print(checksums(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
