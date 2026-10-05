"""Live Ollama prompt/profile smoke checks with a synthetic calculator result.

Uses isolated temporary model tags; no vault, MCP server or cloud expert is used.
Checks direct text, a calculate call, and the answer after its fixture result.

    uv run python nuru/evals/model_prompt_smoke.py --models qwen3.5:4b granite4.2:3b --out /tmp/smoke.jsonl
"""

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import requests

from core.model_profile import family, for_model
from core.modelfile import render_modelfile
from core.providers.ollama import BASE, create_model

MODELFILES = Path(__file__).resolve().parents[1] / "modelfiles"
CALCULATE = {"type": "function", "function": {
    "name": "calculate", "description": "Evaluate a strict scalar arithmetic expression.",
    "parameters": {"type": "object", "properties": {"expression": {"type": "string"}},
                   "required": ["expression"], "additionalProperties": False}}}


def check_model(base: str, model: str, num_ctx: int, tags: list[dict], version: str) -> dict:
    profile = for_model(base)
    think = profile.think(profile.default)
    modelfile = MODELFILES / f"{family(base)}.Modelfile"
    record = {"base": base, "ollama_version": version,
              "base_digest": next((t["digest"] for t in tags if t["name"] == base), None),
              "rendered_sha256": hashlib.sha256(render_modelfile(modelfile, base).encode()).hexdigest(),
              "think": think, "options": profile.options(think).get("options", {}), "num_ctx": num_ctx,
              "fixture": "synthetic calculate result; live Ollama, no MCP", "checks": {}}

    def chat(messages: list[dict], tools: list[dict] | None = None) -> dict:
        body = {"model": model, "messages": messages, "stream": False, "think": think,
                "options": {**record["options"], "num_ctx": num_ctx, "num_predict": 2048}}
        if tools is not None:
            body["tools"] = tools
        response = requests.post(f"{BASE}/api/chat", json=body, timeout=(10, 180))
        response.raise_for_status()
        return response.json()["message"]

    try:
        create_model(model, base, modelfile)
        direct = chat([{"role": "user", "content": "What colour is a clear daytime sky? Reply with one word."}])
        record["direct"] = direct
        record["checks"]["direct_answer"] = direct.get("content", "").strip(" .!?\n").lower() == "blue"
        messages = [{"role": "user", "content": "Use calculate for 7 * 8, then give me its result."}]
        requested = chat(messages, [CALCULATE])
        record["tool_request"] = requested
        calls = requested.get("tool_calls") or []
        if len(calls) != 1 or calls[0].get("function", {}).get("name") != "calculate":
            record["checks"].update(tool_call=False, answer_after_result=False)
            return record
        arguments = calls[0]["function"].get("arguments") or {}
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        expression = re.sub(r"[\s()]", "", str(arguments.get("expression", "")))
        valid = expression in ("7*8", "8*7")
        record["checks"]["tool_call"] = valid
        if not valid:
            record["checks"]["answer_after_result"] = False
            return record
        messages += [{"role": "assistant", "tool_calls": calls},
                     {"role": "tool", "content": json.dumps({"schema_version": 1, "ok": True, "result": "56"})}]
        answer = chat(messages, [CALCULATE])
        record["after_result"] = answer
        record["checks"]["answer_after_result"] = bool(re.search(r"\b56\b", answer.get("content", ""))) and not answer.get("tool_calls")
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        # Release only this check's model and delete only its uniquely named tag.
        try:
            requests.post(f"{BASE}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30).raise_for_status()
            requests.delete(f"{BASE}/api/delete", json={"model": model}, timeout=30).raise_for_status()
        except requests.RequestException as error:
            record["cleanup_error"] = str(error)
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--num-ctx", type=int, default=16384)
    args = parser.parse_args()
    for base in args.models:
        for_model(base)  # fail before any live action for unsupported families
    response = requests.get(f"{BASE}/api/tags", timeout=10)
    response.raise_for_status()
    tags = response.json()["models"]
    response = requests.get(f"{BASE}/api/version", timeout=10)
    response.raise_for_status()
    version = response.json()["version"]
    installed = {t["name"] for t in tags}
    missing = set(args.models) - installed
    if missing:
        parser.error(f"Models are not installed: {', '.join(sorted(missing))}")
    prefix = f"nuru-pr1-smoke-{time.time_ns()}"
    passed = True
    with args.out.open("w") as output:
        for i, base in enumerate(args.models):
            started = time.monotonic()
            print(f"Checking {base}...", flush=True)
            record = check_model(base, f"{prefix}:{i}", args.num_ctx, tags, version)
            record["seconds"] = round(time.monotonic() - started, 2)
            record["passed"] = (len(record["checks"]) == 3 and all(record["checks"].values())
                                and "error" not in record and "cleanup_error" not in record)
            passed = passed and record["passed"]
            output.write(json.dumps(record) + "\n")
            output.flush()
            print(json.dumps({k: record[k] for k in ("base", "checks", "passed", "seconds")}), flush=True)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
