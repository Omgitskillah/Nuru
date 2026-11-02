"""Fetch the bench (nuru-bench) into this checkout at nuru/evals, keeping whatever the old folder held.

    uv run --locked python tools/fetch_bench.py

A checkout from before the bench became a submodule still has a nuru/evals folder, now untracked: its
.runs/ batches and anything else that was ignored. The submodule can't check out into it, so this:
1. moves the whole folder into a new, uniquely named backup under .runs/ at the checkout's root (the
   same disk, so even gigabytes move at once; .runs/ is ignored), and never deletes it;
2. checks the submodule out (`git submodule update --init --checkout nuru/evals`); if that fails, the
   folder is moved back as it was;
3. moves every .runs/ folder from the backup back to the same place in the checkout - unless one is
   already there, which it never overwrites - and lists what it restored and what it left in the backup.
   Everything else the old folder held stays in the backup.
A checkout with no nuru/evals yet (or an empty one) just gets the submodule. One whose nuru/evals has
git metadata is left exactly as it is: if it's the bench - its own work tree, at the commit this checkout
pins, every tracked file as committed - there is nothing to do; if not (a stale or broken .git, another
repository, another commit, a checkout that is incomplete or changed), this says why and changes nothing.
"""
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

EVALS = Path("nuru") / "evals"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _not_the_bench(root: Path, evals: Path) -> str | None:
    """Why the nuru/evals that has git metadata isn't the bench checked out at its pin; None if it is."""
    top = _git(evals, "rev-parse", "--show-toplevel")
    if top.returncode:
        return f"git can't read it ({top.stderr.strip().splitlines()[0] if top.stderr.strip() else 'no reason given'})"
    if Path(top.stdout.strip()).resolve() != evals.resolve():
        return f"its git metadata is for the work tree at {top.stdout.strip()}"
    pin = _git(root, "rev-parse", f"HEAD:{EVALS.as_posix()}").stdout.strip()
    head = _git(evals, "rev-parse", "--verify", "-q", "HEAD").stdout.strip()
    if head != pin:
        if _git(evals, "cat-file", "-e", f"{pin}^{{commit}}").returncode:
            return f"it doesn't have the pinned commit {pin[:7]}: it isn't the bench, or isn't fully fetched"
        return f"it's at {head[:7]}, not the pinned {pin[:7]}"
    changed = _git(evals, "status", "--porcelain", "--untracked-files=no").stdout.splitlines()
    if changed:
        return f"{len(changed)} tracked file(s) differ from the pinned commit: an incomplete or changed checkout"
    return None


def _fetch(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "submodule", "update", "--init", "--checkout", EVALS.as_posix()], check=True)


def fetch(root: Path) -> list[str]:
    """Do the above in the checkout at `root`; returns what it did, one line each."""
    evals = root / EVALS
    if (evals / ".git").exists():
        problem = _not_the_bench(root, evals)
        if problem:
            raise RuntimeError(f"{EVALS.as_posix()} has git metadata, but {problem}. Nothing was changed: look at it "
                               f"(git -C {EVALS.as_posix()} status), move it aside if it isn't needed, and run this again")
        return [f"{EVALS.as_posix()} is already the bench at its pinned commit: nothing to do"]
    if not evals.exists() or not any(evals.iterdir()):
        _fetch(root)
        return [f"fetched the bench into {EVALS.as_posix()}"]
    (root / ".runs").mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=f"evals-backup-{datetime.now():%Y%m%d-%H%M%S}-", dir=root / ".runs"))
    old = backup / "evals"
    evals.rename(old)
    try:
        _fetch(root)
    except subprocess.CalledProcessError as e:
        if evals.exists() and not any(evals.iterdir()):
            evals.rmdir()
        if evals.exists():
            raise RuntimeError(f"couldn't fetch the bench ({e}); the old folder is at {old}") from e
        old.rename(evals)
        backup.rmdir()
        raise RuntimeError(f"couldn't fetch the bench ({e}); {EVALS.as_posix()} is as it was") from e
    done = [f"moved the old {EVALS.as_posix()} to {old}", f"fetched the bench into {EVALS.as_posix()}"]
    for runs in sorted(old.rglob(".runs")):
        if not runs.is_dir():  # also one already moved with its parent
            continue
        target = evals / runs.relative_to(old)
        if target.exists():
            done.append(f"kept {runs} in the backup: {target} already exists")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            runs.rename(target)
            done.append(f"restored {target.relative_to(root).as_posix()}")
    done.append(f"the backup stays at {backup}; delete it when you no longer need it")
    return done


def main() -> int:
    root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                               check=True).stdout.strip())
    try:
        print("\n".join(fetch(root)))
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
