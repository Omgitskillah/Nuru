"""WP01 M5 benchmark: the same scripted turns through Session directly, through nuru-serve over HTTP and
through the plugin's own client and renderer, with no model (nuru/tests/scripted_serve.py).

    uv run --locked python nuru/evals/obsidian_remote/bench_wp01.py [--turns 30] [--warmups 3] [--cycles 20] [--out DIR]

Routes run in alternating order, after warmups. Per turn: acceptance (POST to 202), first event or first
rendered text, completion, and each chunk's overhead from the moment the scripted Session yielded it
(stamped in the chunk) to the moment the route had it (received; rendered, for the plugin). Stop: from
the first text to the acknowledgement of Stop, and to the `cancelled` event. Then 20 open/close cycles of
the plugin's ChatView against the service, counting what each side still holds. Writes raw.json and
summary.json (with the plan's gates) into --out (default .runs/obsidian-remote/bench-<time>, ignored by git),
and prints the summary.

The plugin route runs plugins/obsidian/bench/follow.js on the jsdom stand-in: it times the plugin's code
over a real socket, not Obsidian's own rendering.
"""

import argparse
import hashlib
import http.client
import json
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "nuru" / "tests"))

from core.service import protocol  # noqa: E402
from scripted_serve import ScenarioSession  # noqa: E402
from core.session import Text  # noqa: E402

STAMP = re.compile(r"⟦t=([\d.]+)⟧")
ROUTES = ("direct", "http", "plugin")
GATES = {"accepted_ms": 250, "overheads_ms": 100, "stop_ack_ms": 250}


def now() -> float:
    return time.time() * 1000


def direct_turn(text):
    session, begun, first, overheads = ScenarioSession(), now(), None, []
    for event in session.ask(text, threading.Event()):
        at = now()
        first = first or at
        if isinstance(event, Text):
            overheads += [at - float(s) for s in STAMP.findall(event.text)]
    return {"first_event_ms": first - begun, "completed_ms": now() - begun, "state": "completed", "overheads_ms": overheads}


def direct_stop():
    cancel, asked = threading.Event(), None
    for _ in ScenarioSession().ask("scenario:slow", cancel):
        if asked is None:
            asked = now()
            cancel.set()
    ended = now()  # Session returned: nothing more to stop
    return {"stop_ack_ms": ended - asked, "stop_cancelled_ms": ended - asked, "state": "cancelled"}


class Http:
    """The service over HTTP, followed as the plugin follows it, from Python."""

    def __init__(self, port, token):
        self.port, self.headers = port, {"Host": f"127.0.0.1:{port}", "Authorization": f"Bearer {token}"}
        self.ids = 0
        self.conversation = self.post("/v1/conversations", {"request_id": self.request_id()})["conversation_id"]

    def request_id(self):
        self.ids += 1
        return f"req-bench-{self.ids:08d}"

    def post(self, path, body):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request("POST", path, json.dumps(body), self.headers)
        answer = json.loads(connection.getresponse().read())
        connection.close()
        return answer

    def follow(self, events_path, on_event):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request("GET", events_path + "?after=0", headers=self.headers)
        response, block = connection.getresponse(), b""
        try:
            for line in response:
                block += line
                if line != b"\n":
                    continue
                for event in protocol.parse_sse(block):
                    on_event(event, now())
                    if event["type"] in protocol.TERMINAL:
                        return event["type"]
                block = b""
        finally:
            connection.close()

    def submit(self, text):
        begun = now()
        accepted = self.post(f"/v1/conversations/{self.conversation}/turns", {"request_id": self.request_id(), "text": text})
        return begun, now(), accepted

    def turn(self, text):
        begun, accepted_at, accepted = self.submit(text)
        seen = {"first": None, "overheads": []}

        def on_event(event, at):
            if event["type"] == "text":
                seen["first"] = seen["first"] or at
                seen["overheads"] += [at - float(s) for s in STAMP.findall(event["data"]["text"])]

        state = self.follow(accepted["events"], on_event)
        return {"accepted_ms": accepted_at - begun, "first_event_ms": seen["first"] - begun, "completed_ms": now() - begun,
                "state": state, "overheads_ms": seen["overheads"]}

    def stop(self):
        _, _, accepted = self.submit("scenario:slow")
        seen = {}

        def on_event(event, at):
            if event["type"] == "text" and "asked" not in seen:
                seen["asked"] = at
                self.post(f"/v1/turns/{accepted['turn_id']}/cancel", {"request_id": self.request_id()})
                seen["ack"] = now()

        state = self.follow(accepted["events"], on_event)
        return {"stop_ack_ms": seen["ack"] - seen["asked"], "stop_cancelled_ms": now() - seen["asked"], "state": state}


class Plugin:
    def __init__(self, port, vault, home):
        self.process = subprocess.Popen(["node", "--expose-gc", str(ROOT / "plugins/obsidian/bench/follow.js"), str(port), vault, str(home)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=ROOT / "plugins/obsidian")

    def send(self, **command):
        self.process.stdin.write(json.dumps(command) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"follow.js stopped ({self.process.poll()})")
        return json.loads(line)

    def turn(self, text):
        return self.send(op="turn", text=text)

    def stop(self):
        return self.send(op="stop")

    def close(self):
        self.process.stdin.close()
        self.process.wait(10)


def process_counts(pid):
    rss_kb = int(subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout)
    threads = len(subprocess.run(["ps", "-M", "-p", str(pid)], capture_output=True, text=True).stdout.splitlines()) - 1
    return {"rss_kb": rss_kb, "threads": threads}


def percentile(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(p / 100 * len(ordered) + 0.5) - 1))] if ordered else None


def stats(values):
    return {"n": len(values), "p50": percentile(values, 50), "p95": percentile(values, 95), "max": max(values, default=None)}


def run(args):
    scratch = Path(tempfile.mkdtemp(prefix="nuru-bench-"))
    serve = subprocess.Popen([sys.executable, str(ROOT / "nuru/tests/scripted_serve.py"), "--dir", str(scratch / "serve")],
                             stdout=subprocess.PIPE, text=True, cwd=ROOT)
    started = json.loads(serve.stdout.readline())
    home = scratch / "home"
    (home / ".nuru/service/clients").mkdir(parents=True)
    (home / ".nuru/service/clients/obsidian.token").write_text(started["token"])
    plugin = Plugin(started["port"], started["vault"], home)
    routes = {"direct": (direct_turn, direct_stop), "http": None, "plugin": (plugin.turn, plugin.stop)}
    client = Http(started["port"], started["token"])
    routes["http"] = (client.turn, client.stop)
    samples = {route: {"turn": [], "stop": []} for route in ROUTES}
    try:
        for kind in ("turn", "stop"):
            for i in range(args.warmups + args.turns):
                order = ROUTES[i % 3:] + ROUTES[:i % 3]  # alternate which route goes first
                for route in order:
                    turn, stop = routes[route]
                    result = turn("scenario:bench") if kind == "turn" else stop()
                    if i >= args.warmups:
                        samples[route][kind].append({**result, "order": order.index(route)})
        service_warm = process_counts(serve.pid)
        cycles = plugin.send(op="cycles", n=args.cycles)
        service_after = process_counts(serve.pid)
    finally:
        plugin.close()
        serve.terminate()
        serve.wait(10)
    return samples, {"plugin": cycles, "service": {"warm": service_warm, "after": service_after}}, scratch


def summarise(samples, leaks):
    summary, gates = {}, []
    for route, kinds in samples.items():
        summary[route] = {}
        for kind, results in kinds.items():
            for metric in sorted({k for r in results for k in r if k.endswith("_ms")}):
                values = [v for r in results for v in (r[metric] if isinstance(r[metric], list) else [r[metric]])]
                summary[route][metric] = stats(values)
                if metric in GATES and route != "direct":
                    gates.append({"gate": f"{route} {metric} p95 <= {GATES[metric]} ms", "value": summary[route][metric]["p95"],
                                  "pass": summary[route][metric]["p95"] <= GATES[metric]})
            states = sorted({r["state"] for r in results})
            summary[route][f"{kind}_states"] = states
            gates.append({"gate": f"{route} every {kind} ended {'completed' if kind == 'turn' else 'cancelled'}", "value": states,
                          "pass": states == (["completed"] if kind == "turn" else ["cancelled"])})
    plugin, service = leaks["plugin"], leaks["service"]
    for name in ("listeners", "intervals", "resources"):
        gates.append({"gate": f"plugin {name} after cycles == warm", "value": [plugin["warm"][name], plugin["after"][name]],
                      "pass": plugin["after"][name] == plugin["warm"][name]})
    gates.append({"gate": "plugin heap after GC within 10% of warm", "value": [plugin["warm"]["heap_used"], plugin["after"]["heap_used"]],
                  "pass": plugin["after"]["heap_used"] <= plugin["warm"]["heap_used"] * 1.1})
    gates.append({"gate": "service threads after cycles == warm", "value": [service["warm"]["threads"], service["after"]["threads"]],
                  "pass": service["after"]["threads"] == service["warm"]["threads"]})
    gates.append({"gate": "service RSS within 10% of warm", "value": [service["warm"]["rss_kb"], service["after"]["rss_kb"]],
                  "pass": service["after"]["rss_kb"] <= service["warm"]["rss_kb"] * 1.1})
    return summary, gates


def machine():
    def out(*command):
        try:
            return subprocess.run(command, capture_output=True, text=True, cwd=ROOT).stdout.strip()
        except OSError:
            return None
    return {"macos": platform.mac_ver()[0], "machine": platform.machine(), "python": platform.python_version(),
            "node": out("node", "--version"), "commit": out("git", "rev-parse", "HEAD"),
            "dirty": bool(out("git", "status", "--porcelain"))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--turns", type=int, default=30)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--cycles", type=int, default=20)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    started = datetime.now(timezone.utc)
    samples, leaks, scratch = run(args)
    summary, gates = summarise(samples, leaks)
    out = args.out or ROOT / ".runs" / "obsidian-remote" / f"bench-{started:%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({"samples": samples, "leaks": leaks}, indent=1).encode()
    (out / "raw.json").write_bytes(raw)
    report = {"started": started.isoformat(timespec="seconds"), "command": " ".join(sys.argv), "machine": machine(),
              "settings": vars(args) | {"out": str(out)}, "raw_sha256": hashlib.sha256(raw).hexdigest(),
              "summary": summary, "leaks": leaks, "gates": gates, "all_gates_pass": all(g["pass"] for g in gates)}
    (out / "summary.json").write_text(json.dumps(report, indent=1))
    for route, metrics in summary.items():
        for metric, value in metrics.items():
            if isinstance(value, dict):
                print(f"{route:7} {metric:18} n={value['n']:4} p50={value['p50']:8.2f} p95={value['p95']:8.2f} max={value['max']:8.2f}")
    for gate in gates:
        print(("PASS " if gate["pass"] else "FAIL ") + gate["gate"], gate["value"])
    print(f"wrote {out}; scratch {scratch}")
    return 0 if report["all_gates_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
