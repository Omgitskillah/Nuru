"""Send each case's prompt to each planner model, exactly as the example requests do; record plan, timings, memory.

Usage: python3 run.py OUT.jsonl [--repeats 2] [--models m1,m2]
Nothing in a plan is executed.
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
OLLAMA = "http://localhost:11434"
MODELS = ["granite-planner:350m", "granite-planner:1b", "qwen35-planner:0.8b", "qwen35-planner:2b"]


def post(path, body):
    req = urllib.request.Request(OLLAMA + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=600))


def get(path):
    return json.load(urllib.request.urlopen(OLLAMA + path))


def unload_all():
    for m in get("/api/ps").get("models", []):
        post("/api/generate", {"model": m["name"], "keep_alive": 0})


def request(model, prompt, schema, think=False):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False,
            "format": schema, "keep_alive": "5m"}
    if model.startswith(("qwen", "gemma")):  # models with an off/on thinking switch
        body["think"] = think
    return body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--think", action="store_true", help="Qwen and Gemma only: think=true (Granite 4 can't think)")
    args = ap.parse_args()
    schema = json.loads((HERE / "plan.schema.json").read_text())
    cases = json.loads((HERE / "cases.json").read_text()) + json.loads((HERE / "cases_yesterday.json").read_text())
    with args.out.open("a") as out:
        for model in args.models.split(","):
            unload_all()
            t0 = time.time()
            warm = post("/api/chat", request(model, "Show my five newest notes in Projects.", schema, args.think))
            ps = {m["name"]: m for m in get("/api/ps")["models"]}.get(model, {})
            out.write(json.dumps({"model": model, "warmup": True, "wall_s": time.time() - t0,
                                  "load_s": warm.get("load_duration", 0) / 1e9,
                                  "mem_size": ps.get("size"), "mem_vram": ps.get("size_vram"),
                                  "context_length": ps.get("context_length")}) + "\n")
            for case in cases:
                for rep in range(1, args.repeats + 1):
                    t0 = time.time()
                    try:
                        r = post("/api/chat", request(model, case["prompt"], schema, args.think))
                        err = r.get("error")
                    except Exception as e:  # recorded, not fatal
                        r, err = {}, repr(e)
                    wall = time.time() - t0
                    ps = {m["name"]: m for m in get("/api/ps")["models"]}.get(model, {})
                    out.write(json.dumps({
                        "model": model, "case": case["id"], "repeat": rep, "wall_s": wall,
                        "content": r.get("message", {}).get("content"),
                        "thinking": r.get("message", {}).get("thinking"),
                        "tool_calls": r.get("message", {}).get("tool_calls"),
                        "done_reason": r.get("done_reason"), "error": err,
                        "load_s": r.get("load_duration", 0) / 1e9,
                        "prompt_tokens": r.get("prompt_eval_count"), "output_tokens": r.get("eval_count"),
                        "eval_s": r.get("eval_duration", 0) / 1e9, "total_s": r.get("total_duration", 0) / 1e9,
                        "mem_size": ps.get("size"), "mem_vram": ps.get("size_vram"),
                    }) + "\n")
                    out.flush()
                    print(model, case["id"], rep, f"{wall:.1f}s", r.get("done_reason"), flush=True)
            unload_all()


if __name__ == "__main__":
    main()
