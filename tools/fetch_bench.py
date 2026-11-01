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
A checkout whose nuru/evals is already the bench, or has no nuru/evals yet, just gets the submodule.
"""
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

EVALS = Path("nuru") / "evals"


def _is_checked_out(evals: Path) -> bool:
    return (evals / ".git").exists()


def _fetch(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "submodule", "update", "--init", "--checkout", EVALS.as_posix()], check=True)


def fetch(root: Path) -> list[str]:
    """Do the above in the checkout at `root`; returns what it did, one line each."""
    evals = root / EVALS
    if _is_checked_out(evals):
        return [f"{EVALS.as_posix()} is already the bench: nothing to do"]
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
