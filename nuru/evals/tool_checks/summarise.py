import json, sys
from pathlib import Path
R = Path(__file__).parent
import os
prompts = {p["id"]: p for p in json.loads((R / os.environ.get("PROMPTS", "prompts.json")).read_text())}
ids = [int(a) for a in sys.argv[1:]] or sorted(prompts)
for i in ids:
    f = R / "out" / f"{i:02d}.json"
    if not f.exists():
        print(f"=== {i:02d}: NO RESULT"); continue
    d = json.loads(f.read_text()); p = prompts[i]
    print(f"=== {i:02d} [{p['group']}] {p['prompt']}  (expect: {p['expect']}; turn {d['turn_s']}s)")
    for c in d["calls"]:
        print(f"  {c['tool']}({json.dumps(c['args'], ensure_ascii=False)[:220]}) -> {'ERR ' if c['is_error'] else ''}{c['result'][:260].replace(chr(10), ' ')}")
    other = [e for e in d["events"] if "tool_call" not in e]
    if other: print("  events:", other)
    vd = d["vault_diff"]
    if any(vd.values()): print("  VAULT:", json.dumps(vd, ensure_ascii=False)[:700])
    print("  REPLY:", d["reply"][:600].replace("\n", " | "))
