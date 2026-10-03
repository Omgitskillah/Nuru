"""Run each prompt in prompts.json through driver.py in a fresh process, resetting the vault first."""
import json, os, shutil, signal, subprocess, sys, time
from pathlib import Path
R = Path(__file__).parent
NURU = "/Users/aluchoayisi/Desktop/Nuru/nuru"
only = {int(a) for a in sys.argv[1:]}
for p in json.loads((R / os.environ.get("PROMPTS", "prompts.json")).read_text()):
    if only and p["id"] not in only:
        continue
    out = R / "out" / f"{p['id']:02d}.json"
    subprocess.run(["rsync", "-a", "--delete", f"{R}/vault-pristine/", f"{R}/vault/"], check=True)
    shutil.copy(R / "semantic-pristine.sqlite3", R / "semantic.sqlite3")
    env = {**os.environ, "VAULT_PATH": f"{R}/vault", "OBSIDIAN_SEMANTIC_DB": f"{R}/semantic.sqlite3",
           "NURU_DB_PATH": f"{R}/db/{p['id']:02d}.db", "NURU_LOG_DIR": f"{R}/logs",
           "PATH": "/opt/homebrew/bin:" + os.environ["PATH"]}
    # Laya logs to this run's own decision database, never Nuru's real one.
    cfg = R / "cfg" / f"{p['id']}.yaml"
    cfg.parent.mkdir(exist_ok=True)
    text = open("/Users/aluchoayisi/Desktop/Nuru/config/decision_layer.yaml").read()
    cfg.write_text(text.replace("database: nuru/.db/decisions.sqlite3", f"database: {R}/dec/{p['id']}.sqlite3"))
    (R / "dec").mkdir(exist_ok=True)
    env["NURU_DECISION_CONFIG"] = str(cfg)
    env.pop("VIRTUAL_ENV", None)
    t = time.monotonic()
    with open(R / "out" / f"{p['id']:02d}.log", "w") as log:
        proc = subprocess.Popen(["uv", "run", "python", str(R / "driver.py"), str(out), p["prompt"]],
                                cwd=NURU, env=env, stdout=log, stderr=log, start_new_session=True)
        try:
            proc.wait(timeout=600)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            print(f"{p['id']:02d} TIMEOUT", flush=True)
            continue
    print(f"{p['id']:02d} rc={proc.returncode} {time.monotonic() - t:.0f}s", flush=True)
