"""release/build.py, run on real git repositories: a Nuru with a server repository pinned as a submodule."""
import hashlib
import importlib.util
import json
import runpy
import subprocess
import zipfile
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("release_build", Path(__file__).parents[2] / "release" / "build.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "protocol.file.allow=always",
       "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main"]


def git(cwd, *args):
    subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True)


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def nuru(tmp_path):
    """A Nuru at 0.2.0 pinning a server whose later, unpinned commit and uncommitted edits must stay out."""
    server = tmp_path / "server"
    write(server / "run.sh", "echo pinned\n")
    git(tmp_path, "init", "server")
    git(server, "add", ".")
    git(server, "update-index", "--chmod=+x", "run.sh")
    git(server, "commit", "-m", "pinned")
    root = tmp_path / "nuru"
    write(root / "plugins/obsidian/manifest.json", json.dumps({"id": "nuru", "version": "0.2.0"}))
    write(root / "plugins/obsidian/package.json", json.dumps({"name": "nuru-obsidian", "version": "0.2.0"}))
    write(root / "nuru/pyproject.toml", '[project]\nname = "core"\nversion = "0.2.0"\n')
    write(root / "nuru/src/core/session.py", "café = 1\n")
    git(tmp_path, "init", "nuru")
    git(root, "submodule", "add", server.as_uri(), "servers/math")
    git(root, "add", ".")
    git(root, "commit", "-m", "nuru")
    write(server / "run.sh", "echo later\n")
    git(server, "commit", "-am", "later")
    write(root / "nuru/src/core/session.py", "uncommitted = 1\n")
    write(root / "servers/math/run.sh", "echo uncommitted\n")
    return root


def test_the_bundle_holds_the_tagged_commit_and_each_pinned_server_and_the_checksums_cover_every_asset(nuru, tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    (out / "main.js").write_text("plugin", encoding="utf-8")

    assert build.main(["0.2.0", str(out)], root=nuru) == 0

    with zipfile.ZipFile(out / "nuru-0.2.0.zip") as bundle:
        contents = {info.filename: (info.external_attr >> 16, bundle.read(info)) for info in bundle.infolist()}
    assert contents["nuru-0.2.0/nuru/src/core/session.py"] == (0o100644, "café = 1\n".encode())
    assert contents["nuru-0.2.0/servers/math/run.sh"] == (0o100755, b"echo pinned\n")
    assert "nuru-0.2.0/.gitmodules" in contents
    assert not [name for name in contents if "/.git/" in name or name.endswith("/")]
    sums = (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert sums == [f"{hashlib.sha256((out / name).read_bytes()).hexdigest()}  {name}"
                    for name in ("main.js", "nuru-0.2.0.zip")]
    assert capsys.readouterr().out.split() == [str(out / "nuru-0.2.0.zip"), str(out / "SHA256SUMS")]


def test_the_same_commit_builds_the_same_bundle(nuru, tmp_path):
    for out in ("a", "b"):
        assert build.main(["0.2.0", str(tmp_path / out)], root=nuru) == 0
    assert (tmp_path / "a/nuru-0.2.0.zip").read_bytes() == (tmp_path / "b/nuru-0.2.0.zip").read_bytes()


def test_a_tag_that_isnt_every_files_version_builds_nothing(nuru, tmp_path, capsys):
    write(nuru / "plugins/obsidian/package.json", json.dumps({"version": "0.2.1"}))
    git(nuru, "commit", "-am", "bump only the package")

    assert build.main(["0.2.1", str(tmp_path / "out")], root=nuru) == 1

    assert capsys.readouterr().err.strip() == ("Releasing 0.2.1, but plugins/obsidian/manifest.json says 0.2.0, "
                                               "nuru/pyproject.toml says 0.2.0")
    assert not (tmp_path / "out").exists()


def test_run_without_a_version_and_folder_it_says_how_to_run_it(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["release/build.py"])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(_spec.origin, run_name="__main__")
    assert exited.value.code == 2
    assert "python release/build.py <version> <out folder>" in capsys.readouterr().err
