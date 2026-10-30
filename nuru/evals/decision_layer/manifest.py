"""What a suite batch ran on: written by run_suite.py as manifest.json in the batch folder, so two
batches can be told apart without remembering what was switched in between.

    uv run python nuru/evals/decision_layer/manifest.py <batch-a> <batch-b>   # the fields that differ
"""

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests
import yaml
from dotenv import dotenv_values

from core.modelfile import render_modelfile
from obsidian_mcp.semantic import load_config as load_semantic_config
from common import DUMMY_VAULT, OLLAMA, REPO


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _decision_config_sha256(path: Path) -> str | None:
    """The decision config's settings, without its batch-local database path - which differs in every
    batch and would make two batches with the same settings look different."""
    if not path.is_file():
        return None
    config = yaml.safe_load(path.read_text()) or {}
    config.pop("database", None)
    return hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()


def _tree_sha256(root: Path) -> str:
    """One hash over every file's relative path and content - a fixture edit changes it."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".obsidian-mcp" not in p.name):
        digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout


def _ollama_model(name: str) -> dict:
    try:
        version = requests.get(f"{OLLAMA}/api/version", timeout=5).json().get("version")
        tags = requests.get(f"{OLLAMA}/api/tags", timeout=5).json().get("models", [])
        details = requests.post(f"{OLLAMA}/api/show", json={"model": name}, timeout=10).json().get("details", {})
    except requests.RequestException as e:
        return {"error": repr(e)[:200]}
    digest = next((m.get("digest") for m in tags if m.get("name") == name), None)
    return {"ollama_version": version, "digest": digest, "quantization": details.get("quantization_level"),
            "parameter_size": details.get("parameter_size")}


def _modelfile(session) -> dict:
    """Which Modelfile the base model runs with: Nuru's checked-in one, or the user's settings for a
    family added in Settings (core.user_models, TODO F22), rendered as Nuru builds it."""
    from core import user_models
    from core.modelfile import render_header

    header = user_models.header(session.BASE_MODEL)
    if header is None:
        modelfile = session._modelfile_for(session.BASE_MODEL)
        return {"modelfile": str(modelfile.relative_to(REPO)), "modelfile_sha256": _sha256(modelfile),
                "rendered_modelfile_sha256": hashlib.sha256(render_modelfile(modelfile, session.BASE_MODEL).encode()).hexdigest()}
    bundle = user_models.active()[user_models.family_of(session.BASE_MODEL)]
    rendered = render_header(header, session.BASE_MODEL, session.MODELFILES_DIR / "system.md")
    return {"modelfile": f"user settings: {user_models.family_of(session.BASE_MODEL)} revision {bundle.revision}",
            "modelfile_sha256": hashlib.sha256(header.encode()).hexdigest(),
            "rendered_modelfile_sha256": hashlib.sha256(rendered.encode()).hexdigest()}


def build(args: dict, decision_config: Path) -> dict:
    """The manifest for a batch started now with `args` (run_suite's arguments)."""
    from core import session  # the same settings and Modelfile choice Nuru itself uses

    model = _modelfile(session)
    dirty = _git("status", "--porcelain")
    obsidian = {**dotenv_values(REPO / ".env.obsidian"), **os.environ}
    semantic = load_semantic_config(DUMMY_VAULT, obsidian)
    python_env = dotenv_values(REPO / ".env.python")
    return {
        "started": datetime.now().astimezone().isoformat(timespec="seconds"),
        "reference_date": datetime.now().date().isoformat(),
        "args": args,
        "repo": {"head": _git("rev-parse", "HEAD").strip(), "dirty": bool(dirty.strip()),
                 "diff_sha256": hashlib.sha256(_git("diff", "HEAD").encode()).hexdigest() if dirty.strip() else None,
                 "submodules": {line.split()[1]: line.split()[0].lstrip("+-U")
                                for line in _git("submodule", "status").splitlines() if line.strip()}},
        "model": {"main": session.MAIN_MODEL or "ollama", "base": session.BASE_MODEL, **_ollama_model(session.BASE_MODEL),
                  **model,
                  "profile": {"think_map": session.PROFILE.think_map, "default": session.PROFILE.default,
                              "sampling": session.PROFILE.sampling},
                  "max_num_ctx": session.MAX_NUM_CTX},
        "tools": sorted(session.MODEL_TOOLS),
        "cases_sha256": _sha256(Path(args["cases"])) if args.get("cases") else None,
        "scorer_sha256": _sha256(Path(__file__).with_name("score_runs.py")),
        "decision_config_sha256": _decision_config_sha256(decision_config),
        "vault": {"path": str(DUMMY_VAULT), "sha256": _tree_sha256(DUMMY_VAULT)},
        "embedding_model": semantic.model,
        "python_sandbox": {"image": python_env.get("PYTHON_MCP_IMAGE"),
                           "available": session.python_server_params() is not None},
        "machine": {"macos": platform.mac_ver()[0], "arch": platform.machine()},
    }


def differences(a: dict, b: dict, prefix: str = "") -> list[str]:
    """Every field that differs between two manifests, as 'path: a -> b' (started is left out)."""
    found = []
    for key in sorted(set(a) | set(b)):
        if not prefix and key == "started":
            continue
        va, vb = a.get(key), b.get(key)
        if isinstance(va, dict) and isinstance(vb, dict):
            found += differences(va, vb, f"{prefix}{key}.")
        elif va != vb:
            found.append(f"{prefix}{key}: {va!r} -> {vb!r}")
    return found


if __name__ == "__main__":
    first, second = (json.loads((Path(p) / "manifest.json").read_text()) for p in sys.argv[1:3])
    print("\n".join(differences(first, second)) or "no differences")
