// End to end: the plugin's client and turn follower against the real nuru-serve host and HTTP server,
// running scripted scenarios (nuru/tests/scripted_serve.py: no model, no MCP). Every terminal state and
// every approval result, Stop while running and while queued, and a reconnect. Needs `uv` on PATH.
"use strict";

const { ROOT, FIXTURES, until } = require("../helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");
const { spawn, execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");

const { Display } = require("../../lib/protocol");
const { connect, vaultId, vaultUuid } = require("../../lib/connection");
const { TurnRunner } = require("../../lib/turns");

const ANSWER_SECONDS = 3;
let server;
let started;
let client;
let conversation;

test.before(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-e2e-"));
  server = spawn("uv", ["run", "--locked", "python", path.join(ROOT, "nuru", "tests", "scripted_serve.py"), "--dir", dir,
    "--answer-seconds", String(ANSWER_SECONDS)], { cwd: ROOT, stdio: ["ignore", "pipe", "inherit"] });
  started = JSON.parse(await new Promise((resolve, reject) => {
    readline.createInterface({ input: server.stdout }).once("line", resolve);
    server.once("exit", (code) => reject(new Error(`scripted_serve exited ${code}`)));
  }));
  const home = path.join(dir, "home");
  fs.mkdirSync(path.join(home, ".nuru", "service", "clients"), { recursive: true });
  fs.writeFileSync(path.join(home, ".nuru", "service", "clients", "obsidian.token"), started.token);
  ({ client } = await connect({ port: started.port, vaultPath: started.vault, home }));
  ({ conversation_id: conversation } = await client.createConversation("e2e"));
});

// On Windows killing uv leaves its python running, holding our pipe open, so the run never ends: kill the tree.
test.after(() => new Promise((resolve) => {
  server.once("exit", resolve);
  if (process.platform === "win32") execFileSync("taskkill", ["/pid", String(server.pid), "/t", "/f"], { stdio: "ignore" });
  else server.kill("SIGTERM");
}), { timeout: 30000 });

// Sends `text` and follows the turn; `during(runner)` runs alongside, to answer or stop it.
async function turn(text, during = async () => {}) {
  const { turn_id: turnId } = await client.submit(conversation, text);
  const runner = new TurnRunner({ client, turnId });
  await Promise.all([runner.run(), during(runner)]);
  return runner.display;
}

const question = (runner) => until(() => runner.display.question, 10000).then(() => runner.display.question);

test("Python's vault ID and Display are the plugin's", () => {
  assert.equal(vaultId(started.vault), started.vault_id);
  assert.equal(vaultUuid(started.vault), started.vault_uuid, "the plugin reads the ID the service wrote");
  const python = JSON.parse(execFileSync("uv", ["run", "--locked", "python", "-c", `
import dataclasses, json
from core.service.protocol import Display
streams = json.load(open("nuru/tests/fixtures/protocol_v1.json", encoding="utf-8"))["streams"]
out = {}
for name, events in streams.items():
    shown = Display()
    for event in events:
        shown.apply(event)
    out[name] = dataclasses.asdict(shown)
print(json.dumps(out))`], { cwd: ROOT, encoding: "utf8" }));
  for (const [name, events] of Object.entries(FIXTURES.streams)) {
    const display = new Display();
    for (const event of events) display.apply(event);
    const { position, ...shown } = JSON.parse(JSON.stringify(display));
    assert.deepEqual(shown, python[name], name);
  }
});

test("completed, with tools and what each write did", async () => {
  const plain = await turn("scenario:completed");
  assert.equal(plain.state, "completed");
  assert.match(plain.reply, /\[\[nuru\/Trip\.md\]\]/);
  const tools = await turn("scenario:tools");
  assert.deepEqual(tools.tools.map((t) => [t.name, t.ok]), [["search_notes", true], ["read_note", true], ["write_note", true]]);
  assert.deepEqual(tools.receipts.map((r) => [r.state, r.path]), [["applied", "nuru/summary.md"]], "the file the write changed");
  assert.equal((await turn("scenario:refused-write")).receipts[0].state, "refused");
});

test("failed, including a write whose outcome is unknown", async () => {
  const failed = await turn("scenario:failed");
  assert.deepEqual([failed.state, failed.message], ["failed", "Nuru couldn't finish: the notes server stopped."]);
  const uncertain = await turn("scenario:uncertain-write");
  assert.equal(uncertain.state, "failed");
  assert.equal(uncertain.receipts[0].state, "uncertain");
});

test("approvals: Yes, No, no answer in time, and Stop while asked", async () => {
  const answer = (chosen) => async (runner) => {
    const asked = await question(runner);
    await client.answer(asked.question_id, asked.operation, chosen);
  };
  const yes = await turn("scenario:approval", answer(["Yes"]));
  assert.deepEqual([yes.decisions[0].decision, yes.receipts[0].state, yes.state], ["Yes", "applied", "completed"]);
  const no = await turn("scenario:approval", answer(["No"]));
  assert.deepEqual([no.decisions[0].decision, no.receipts[0].state], ["No", "refused"]);
  const silent = await turn("scenario:approval");
  assert.deepEqual([silent.decisions[0].outcome, silent.decisions[0].decision, silent.receipts[0].state], ["expired", "No", "refused"]);
  const stopped = await turn("scenario:approval", async (runner) => { await question(runner); await runner.cancel(); });
  assert.deepEqual([stopped.decisions[0].outcome, stopped.decisions[0].decision, stopped.state], ["cancelled", "No", "cancelled"]);
  const late = await turn("scenario:approval");
  assert.equal(late.decisions[0].outcome, "expired");
  const asked = late.decisions[0];
  await assert.rejects(client.answer(asked.question_id, "0".repeat(64), ["Yes"]), (e) => e.status === 409);
});

test("a choice with the user's own words, and the model's own question", async () => {
  const chose = await turn("scenario:choice", async (runner) => {
    const asked = await question(runner);
    await client.answer(asked.question_id, asked.operation, [], "user/Budget.md");
  });
  assert.equal(chose.reply, "You chose: user/Budget.md.");
  const ask = await turn("scenario:ask");
  assert.deepEqual([ask.state, ask.ask.kind], ["completed", "yes_no"]);
});

test("Stop while running keeps the reply so far; Stop while queued ends it before it starts", async () => {
  const { turn_id: first } = await client.submit(conversation, "scenario:slow");
  const { turn_id: second } = await client.submit(conversation, "scenario:completed");
  const running = new TurnRunner({ client, turnId: first });
  const queued = new TurnRunner({ client, turnId: second });
  const following = [running.run(), queued.run()];
  await until(() => queued.display.state === "queued" && running.display.reply.length > 0, 10000);
  await queued.cancel();
  await running.cancel();
  await Promise.all(following);
  assert.deepEqual(queued.display.state, "cancelled");
  assert.ok(!queued.display.reply);
  assert.equal(running.display.state, "cancelled");
  assert.match(running.display.reply, /^0 /, "the reply so far is kept");
});

test("a dropped connection picks up where it left off, showing nothing twice", async () => {
  const { turn_id: turnId } = await client.submit(conversation, "scenario:long");
  const first = new TurnRunner({ client, turnId });
  const following = first.run();
  await until(() => first.display.seq >= 5);
  first.stop(); // the panel closed mid-turn
  await following;
  const second = new TurnRunner({ client, turnId, display: first.display });
  await second.run();
  assert.equal(second.display.state, "completed");
  assert.equal(second.display.truncated, true);
  const lines = second.display.reply.split("\n").filter(Boolean).map((line) => line.split(":")[0]);
  assert.deepEqual(lines, [...new Set(lines)], "no line twice");
  assert.equal(lines[0], "Line 0");
});

test("hostile HTML arrives as text for the renderer to neutralise", async () => {
  const hostile = await turn("scenario:hostile");
  assert.match(hostile.reply, /<script>alert\("x"\)<\/script>/);
});
