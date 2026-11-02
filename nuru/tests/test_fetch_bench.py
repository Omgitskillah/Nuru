"""tools/fetch_bench.py with real git: a checkout made before the bench became a submodule keeps
everything its old nuru/evals held - .runs/ batches back in place, the rest in a backup it never
deletes - and a failed fetch leaves the old folder as it was. A nuru/evals with git metadata that isn't
the bench at its pin (broken, stale, another repository, another commit, changed) is refused, untouched."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "tools" / "fetch_bench.py"
spec = importlib.util.spec_from_file_location("fetch_bench", PATH)
fetch_bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_bench)


def git(cwd, *args):
    return subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A bench repo, and a Nuru repo that registers it at nuru/evals with update = none, as #82 does."""
    for key, value in {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "protocol.file.allow", "GIT_CONFIG_VALUE_0": "always",
                       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                       "GIT_COMMITTER_EMAIL": "t@example.com"}.items():
        monkeypatch.setenv(key, value)
    bench = tmp_path / "bench"
    (bench / "decision_layer").mkdir(parents=True)
    (bench / "decision_layer" / "run_suite.py").write_text("# the bench\n")
    (bench / ".gitignore").write_text(".runs/\n")  # as nuru-bench's, and Nuru's own below
    git(tmp_path, "init", "-q", "-b", "main", str(bench))
    git(bench, "add", "-A")
    git(bench, "commit", "-q", "-m", "bench")
    nuru = tmp_path / "nuru"
    git(tmp_path, "init", "-q", "-b", "main", str(nuru))
    (nuru / ".gitignore").write_text(".runs/\n")
    git(nuru, "add", ".gitignore")
    git(nuru, "submodule", "add", "-q", bench.as_uri(), "nuru/evals")
    git(nuru, "config", "-f", ".gitmodules", "submodule.nuru/evals.update", "none")
    git(nuru, "commit", "-q", "-am", "bench as a submodule")
    git(nuru, "submodule", "deinit", "-q", "-f", "nuru/evals")  # as a fresh clone has it: registered, empty
    return nuru


def old_folder(nuru):
    """What a checkout from before kept in nuru/evals: batches, a nested .runs, other untracked files."""
    evals = nuru / "nuru" / "evals"
    (evals / "decision_layer" / ".runs" / "suite-x").mkdir(parents=True)
    (evals / "decision_layer" / ".runs" / "suite-x" / "results.jsonl").write_text("batch\n")
    (evals / "tool_checks" / ".runs").mkdir(parents=True)
    (evals / "tool_checks" / ".runs" / "out.txt").write_text("check\n")
    (evals / "decision_layer" / "notes-to-self.txt").write_text("mine\n")
    (evals / "vault_steward").mkdir()
    (evals / "vault_steward" / ".runs").write_text("a file, not a batch folder\n")
    return evals


def test_an_old_folder_moves_to_a_backup_its_runs_come_back_and_the_rest_stays(checkout):
    evals = old_folder(checkout)
    done = fetch_bench.fetch(checkout)
    assert (evals / "decision_layer" / "run_suite.py").read_text() == "# the bench\n", "the submodule checked out"
    assert (evals / "decision_layer" / ".runs" / "suite-x" / "results.jsonl").read_text() == "batch\n"
    assert (evals / "tool_checks" / ".runs" / "out.txt").read_text() == "check\n"
    [backup] = (checkout / ".runs").glob("evals-backup-*")
    assert (backup / "evals" / "decision_layer" / "notes-to-self.txt").read_text() == "mine\n", "kept, not deleted"
    assert not (evals / "decision_layer" / "notes-to-self.txt").exists()
    assert (backup / "evals" / "vault_steward" / ".runs").is_file(), "a .runs that isn't a folder stays put"
    assert "restored nuru/evals/decision_layer/.runs" in done and "restored nuru/evals/tool_checks/.runs" in done
    assert done[-1].startswith(f"the backup stays at {backup}")
    assert git(checkout, "status", "--porcelain") == "", "the checkout is clean: .runs/ is ignored by the bench"


def test_two_runs_never_share_a_backup_and_an_existing_runs_is_never_overwritten(checkout, monkeypatch):
    evals = old_folder(checkout)
    real_fetch = fetch_bench._fetch

    def fetch_into_a_bench_that_already_has_runs(root):
        real_fetch(root)
        (evals / "tool_checks" / ".runs").mkdir(parents=True)
        (evals / "tool_checks" / ".runs" / "out.txt").write_text("already here\n")

    monkeypatch.setattr(fetch_bench, "_fetch", fetch_into_a_bench_that_already_has_runs)
    done = fetch_bench.fetch(checkout)
    assert (evals / "tool_checks" / ".runs" / "out.txt").read_text() == "already here\n"
    [backup] = (checkout / ".runs").glob("evals-backup-*")
    assert (backup / "evals" / "tool_checks" / ".runs" / "out.txt").read_text() == "check\n"
    assert any(line.startswith("kept ") and "already exists" in line for line in done)
    (checkout / ".runs" / "evals-backup-fixed").mkdir()  # a folder in the way never decides where it goes
    git(checkout, "submodule", "deinit", "-q", "-f", "nuru/evals")
    old_folder(checkout)
    monkeypatch.setattr(fetch_bench, "_fetch", real_fetch)
    fetch_bench.fetch(checkout)
    assert len(list((checkout / ".runs").glob("evals-backup-2*"))) == 2


def test_a_failed_fetch_puts_the_old_folder_back_as_it_was(checkout, monkeypatch):
    evals = old_folder(checkout)
    before = sorted(p.relative_to(evals).as_posix() for p in evals.rglob("*"))
    # Moved, not deleted: git's objects are read-only, which Windows won't delete.
    (checkout.parent / "bench").rename(checkout.parent / "bench-gone")  # the bench can't be reached
    (checkout / ".git" / "modules").rename(checkout / ".git" / "modules-gone")  # and git has no copy to check out
    with pytest.raises(RuntimeError, match="is as it was"):
        fetch_bench.fetch(checkout)
    assert sorted(p.relative_to(evals).as_posix() for p in evals.rglob("*")) == before
    assert list((checkout / ".runs").iterdir()) == []


def test_an_empty_folder_a_failed_clone_leaves_is_replaced_by_the_old_one(checkout, monkeypatch):
    evals = old_folder(checkout)

    def clone_fails_after_making_the_folder(root):
        (root / "nuru" / "evals").mkdir()
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(fetch_bench, "_fetch", clone_fails_after_making_the_folder)
    with pytest.raises(RuntimeError, match="is as it was"):
        fetch_bench.fetch(checkout)
    assert (evals / "decision_layer" / ".runs" / "suite-x" / "results.jsonl").read_text() == "batch\n"


def test_a_fetch_that_fails_halfway_says_where_the_old_folder_is(checkout, monkeypatch):
    evals = old_folder(checkout)

    def half(root):
        (root / "nuru" / "evals").mkdir(exist_ok=True)
        (root / "nuru" / "evals" / "partial").write_text("x")
        raise subprocess.CalledProcessError(1, "git")

    monkeypatch.setattr(fetch_bench, "_fetch", half)
    with pytest.raises(RuntimeError, match="the old folder is at") as failed:
        fetch_bench.fetch(checkout)
    [backup] = (checkout / ".runs").glob("evals-backup-*")
    assert str(backup / "evals") in str(failed.value)
    assert (backup / "evals" / "decision_layer" / ".runs" / "suite-x" / "results.jsonl").exists()


def tree(folder):
    return sorted((p.relative_to(folder).as_posix(), p.read_bytes() if p.is_file() else None) for p in folder.rglob("*"))


def refused(checkout, reason):
    """fetch refuses, naming `reason`, and changes nothing: not nuru/evals, and no backup is made."""
    evals = checkout / "nuru" / "evals"
    before = tree(evals)
    with pytest.raises(RuntimeError, match=reason) as failed:
        fetch_bench.fetch(checkout)
    assert "Nothing was changed" in str(failed.value)
    assert tree(evals) == before and not (checkout / ".runs").exists()


def test_a_folder_whose_git_file_is_broken_isnt_taken_for_the_bench(checkout):
    evals = old_folder(checkout)
    (evals / ".git").write_text("not a git repository\n")
    refused(checkout, "git can't read it")


def test_after_a_fetch_that_failed_halfway_a_retry_refuses_the_stale_checkout_and_the_backup_stays(checkout, monkeypatch):
    evals = old_folder(checkout)

    def half(root):  # the clone wrote its gitfile, then failed before the repository it points to existed
        (root / "nuru" / "evals").mkdir()
        (root / "nuru" / "evals" / ".git").write_text("gitdir: ../../.git/modules/nuru/evals-unfinished\n")
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(fetch_bench, "_fetch", half)
    with pytest.raises(RuntimeError, match="the old folder is at"):
        fetch_bench.fetch(checkout)
    [backup] = (checkout / ".runs").glob("evals-backup-*")
    kept = tree(backup)
    monkeypatch.setattr(fetch_bench, "_fetch", lambda root: pytest.fail("a retry must not fetch over it"))
    with pytest.raises(RuntimeError, match="git can't read it"):
        fetch_bench.fetch(checkout)
    assert [p.name for p in evals.iterdir()] == [".git"]
    assert tree(backup) == kept and (backup / "evals" / "decision_layer" / ".runs" / "suite-x").is_dir()


def test_another_repository_at_nuru_evals_isnt_the_bench(checkout):
    evals = checkout / "nuru" / "evals"
    git(checkout, "init", "-q", str(evals))
    (evals / "mine.txt").write_text("someone's own work\n")
    git(evals, "add", "-A")
    git(evals, "commit", "-q", "-m", "mine")
    refused(checkout, "doesn't have the pinned commit")


def test_the_bench_at_another_commit_or_with_changed_files_is_left_alone(checkout):
    evals = checkout / "nuru" / "evals"
    fetch_bench.fetch(checkout)
    (evals / "decision_layer" / "run_suite.py").write_text("# edited\n")
    refused(checkout, r"1 tracked file\(s\) differ from the pinned commit")
    git(evals, "commit", "-q", "-am", "a newer bench")
    refused(checkout, "not the pinned")
    git(evals, "checkout", "-q", "HEAD~1")
    assert fetch_bench.fetch(checkout) == ["nuru/evals is already the bench at its pinned commit: nothing to do"]


def test_git_metadata_for_another_work_tree_isnt_this_checkouts_bench(checkout, tmp_path):
    fetch_bench.fetch(checkout)
    git(checkout, "config", "-f", str(checkout / ".git" / "modules" / "nuru" / "evals" / "config"),
        "core.worktree", str(tmp_path / "elsewhere"))
    (tmp_path / "elsewhere").mkdir()
    refused(checkout, "its git metadata is for the work tree at")


@pytest.mark.parametrize("state", ["empty", "missing", "bench"])
def test_with_no_old_folder_it_just_fetches(checkout, state):
    evals = checkout / "nuru" / "evals"
    if state == "missing":
        evals.rmdir()
    if state == "bench":
        fetch_bench.fetch(checkout)
    done = fetch_bench.fetch(checkout)
    assert (evals / "decision_layer" / "run_suite.py").exists()
    assert done == (["nuru/evals is already the bench at its pinned commit: nothing to do"] if state == "bench"
                    else ["fetched the bench into nuru/evals"])
    assert not (checkout / ".runs").exists()


def test_run_as_a_script_it_fetches_the_checkout_it_is_in(checkout, monkeypatch):
    import runpy
    monkeypatch.chdir(checkout / "nuru")
    with pytest.raises(SystemExit) as exit:
        runpy.run_path(str(PATH), run_name="__main__")
    assert exit.value.code == 0 and (checkout / "nuru" / "evals" / "decision_layer" / "run_suite.py").exists()


def test_the_command_reports_and_exits_by_the_result(checkout, monkeypatch, capsys):
    old_folder(checkout)
    monkeypatch.chdir(checkout)
    assert fetch_bench.main() == 0
    assert "restored nuru/evals/decision_layer/.runs" in capsys.readouterr().out
    monkeypatch.setattr(fetch_bench, "fetch", lambda root: (_ for _ in ()).throw(RuntimeError("couldn't fetch the bench (x)")))
    assert fetch_bench.main() == 1
    assert "couldn't fetch the bench" in capsys.readouterr().err
