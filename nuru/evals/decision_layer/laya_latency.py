"""Eval 7 - Laya's per-call latency on this machine, MPS against CPU, per question set.

Each question set with a representative state, timed through the same LayaPredictor the server uses (so the
lock, the token-budget check and routing are included). Three warm-up calls, then `--calls` timed.

    uv run python nuru/evals/decision_layer/laya_latency.py [--calls 20] [--devices mps,cpu]
"""

import argparse
import statistics
import time

from common import DUMMY_VAULT, PRIYA, save

from laya_mcp.config import load_config
from laya_mcp.predictor import LayaPredictor
from laya_mcp.question_sets import load_question_sets

REQUEST = "Add a line to Priya's note saying she prefers async messages on Fridays."
STEPS = [{"tool": "search_notes", "status": "ok", "result": f"4 found: Priya Raman ({PRIYA}), Daily 2026-09-17 and 2 more"},
         {"tool": "read_note", "status": "ok", "result": "read 1268 characters: # Priya Raman ## Role Firmware engineer"}]


def cases(sets):
    text = (DUMMY_VAULT / "nuru/battery-life-modeling.md").read_text()
    body = text.split("---\n", 2)[2] if text.startswith("---\n") else text
    return [
        ("tool_gate_start", {"request": REQUEST}, sets["tool_gate_start"].render()),
        ("tool_gate_next", {"request": REQUEST, "steps": STEPS}, sets["tool_gate_next"].render()),
        ("assess_effort", {"request": REQUEST, "step_type": "arguments", "steps": STEPS, "failures": {}},
         sets["assess_effort"].render()),
        ("gate_write", {"request": REQUEST, "tool": "append_note",
                        "arguments": {"id": PRIYA, "content": "Prefers async messages on Fridays"}},
         sets["gate_write"].render()),
        ("check_claim", {"reply": "Done - I added the line to Priya's note, and linked it from the project note. " * 3},
         sets["check_claim"].render()),
        # 800 characters: a full 1,500-character chunk can exceed rerank's 440-token budget (then it
        # is rejected, and rerank keeps the search's order) - measured here, not the failure path.
        ("rerank", {"query": "power budget", "passage": body[:800]}, sets["rerank"].render()),
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=int, default=20)
    parser.add_argument("--devices", default="mps,cpu")
    args = parser.parse_args()
    sets = load_question_sets()
    results = {}
    for device in args.devices.split(","):
        settings = load_config().laya.model_copy(update={"device": device})
        predictor = LayaPredictor(settings)
        started = time.monotonic()
        predictor.start()
        while predictor.status()["state"] == "loading":
            time.sleep(0.2)
        status = predictor.status()
        if status["state"] != "ready":
            results[device] = {"error": status["error"]}
            print(device, "FAILED", status["error"])
            continue
        results[device] = {"load_seconds": round(time.monotonic() - started, 1), "actual_device": status["device"]}
        print(f"{device}: loaded in {results[device]['load_seconds']}s on {status['device']}")
        for name, state, questions in cases(sets):
            qset = sets[name]
            for _ in range(3):
                predictor.predict(qset, state, questions)
            timings, tokens, checkpoint = [], 0, ""
            for _ in range(args.calls):
                t = time.perf_counter()
                prediction = predictor.predict(qset, state, questions)
                timings.append((time.perf_counter() - t) * 1000)
                tokens, checkpoint = prediction.state_tokens, prediction.checkpoint
            timings.sort()
            results[device][name] = {"p50_ms": round(statistics.median(timings), 1),
                                     "p95_ms": round(timings[int(0.95 * (len(timings) - 1))], 1),
                                     "questions": len(questions), "state_tokens": tokens, "checkpoint": checkpoint}
            r = results[device][name]
            print(f"  {name:20} {checkpoint:12} {r['questions']}q {tokens:5} tok  p50 {r['p50_ms']:7} ms  "
                  f"p95 {r['p95_ms']:7} ms", flush=True)
    print(save("laya_latency", results))


if __name__ == "__main__":
    main()
