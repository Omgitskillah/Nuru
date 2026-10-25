"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const { added, processes, remember, removeEverything } = require("../lib/remove");

function home(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "nuru remove home "));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

// A stand-in Ollama: answers while `running`, records the models deleted, and has only `models`; with
// `failing`, a delete fails.
async function ollama(t, { models = [], failing = false } = {}) {
  const state = { running: true, deleted: [] };
  const srv = http.createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (!state.running) return response.writeHead(503).end();
      if (request.url === "/api/version") return response.end(JSON.stringify({ version: "0.40.4" }));
      if (request.url === "/api/tags") return response.end(JSON.stringify({ models: [] }));
      const { model } = JSON.parse(body);
      if (failing) return response.writeHead(500).end(JSON.stringify({ error: "database is locked" }));
      if (!models.includes(model)) return response.writeHead(404).end(JSON.stringify({ error: "not found" }));
      state.deleted.push(model);
      return response.end();
    });
  });
  await new Promise((resolve) => srv.listen(0, "127.0.0.1", resolve));
  t.after(() => { srv.closeAllConnections(); srv.close(); });
  return Object.assign(state, { base: `http://127.0.0.1:${srv.address().port}` });
}

// A home with Nuru installed and an Ollama.app Nuru added, and a vault with notes and Nuru's data.
function installed(t, { app = "Applications/Ollama.app", models = ["qwen3.5:4b", "nomic-embed-text:v1.5"] } = {}) {
  const dir = home(t);
  const generation = path.join(dir, ".nuru", "app", "1.0.1", "1");
  const uv = path.join(generation, "uv", "uv");
  fs.mkdirSync(path.dirname(uv), { recursive: true });
  fs.writeFileSync(uv, "");
  fs.writeFileSync(path.join(dir, ".nuru", "install.json"), JSON.stringify({ nuru: generation, uv, version: "1.0.1" }));
  const appPath = app && path.join(dir, app);
  if (app) {
    fs.mkdirSync(path.dirname(appPath), { recursive: true });
    fs.mkdirSync(appPath);
    fs.writeFileSync(path.join(path.dirname(appPath), "unins000.exe"), "");
    remember(dir, { app: appPath });
  }
  for (const model of models) remember(dir, { model });
  const vault = path.join(dir, "Vault");
  fs.mkdirSync(path.join(vault, ".nuru", "db"), { recursive: true });
  fs.writeFileSync(path.join(vault, "Note.md"), "# mine\n");
  return { dir, vault, uv, app: appPath };
}

// Stands in for running programs: records each, and `effects[command name]` runs as it would.
function runner(effects = {}) {
  const calls = [];
  return { calls, run: async (command, args) => {
    calls.push([path.basename(command), args]);
    return (effects[path.basename(command)] || (() => ({ code: 0, output: "" })))(args);
  } };
}

test("Nuru records what it added: the Ollama it installed and each model once; nothing recorded is nothing", (t) => {
  const dir = home(t);
  assert.deepEqual(added(dir), { ollama: null, models: [] });
  remember(dir, { model: "a" });
  remember(dir, { model: "a" });
  remember(dir, { app: "/x/Ollama.app" });
  assert.deepEqual(added(dir), { ollama: "/x/Ollama.app", models: ["a"] });
  fs.writeFileSync(path.join(dir, ".nuru", "added.json"), "{broken");
  assert.deepEqual(added(dir), { ollama: null, models: [] });
});

// Stands in for the process list: `running` are the programs' paths; kill ends one.
function processList(...running) {
  const state = { running: running.map((command, i) => ({ pid: 100 + i, command })), killed: [] };
  return Object.assign(state, { list: () => state.running.map((p) => ({ ...p })),
    kill: (pid) => { state.killed.push(pid); state.running = state.running.filter((p) => p.pid !== pid); } });
}

test("on macOS everything Nuru added goes: its models, only the Ollama it installed, ~/.nuru and the vault's .nuru; the notes stay", async (t) => {
  const { dir, vault, app } = installed(t);
  const server = await ollama(t, { models: ["qwen3.5:4b"] }); // the embedding model is gone already
  const procs = processList(`${app}/Contents/MacOS/Ollama`, `${app}/Contents/Resources/ollama`, "/Applications/Ollama.app/Contents/Resources/ollama",
    `${app}-copy/Contents/MacOS/Ollama`);
  const { calls, run } = runner();
  const said = [];

  const result = await removeEverything({ home: dir, vault, platform: "darwin", runner: run, list: procs.list, kill: procs.kill,
    base: server.base, say: (s) => said.push(s) });

  assert.deepEqual(result, { problems: [], left: null });
  assert.deepEqual(server.deleted, ["qwen3.5:4b"]);
  assert.deepEqual(procs.killed, [100, 101], "only processes running from inside the app Nuru installed");
  assert.deepEqual(calls, [], "nothing else is run: uv's shared cache is left alone");
  assert.ok(!fs.existsSync(app) && !fs.existsSync(path.join(dir, ".nuru")) && !fs.existsSync(path.join(vault, ".nuru")));
  assert.equal(fs.readFileSync(path.join(vault, "Note.md"), "utf8"), "# mine\n");
  assert.deepEqual(said, ["Deleting Nuru's models from Ollama…", "Removing Ollama…", "Deleting Nuru's install and this vault's Nuru data…"]);
});

test("on Windows the Ollama Nuru installed goes with its own uninstaller, run silently, and never while another Ollama runs", async (t) => {
  const { dir, vault, app } = installed(t, { app: "Programs/Ollama/ollama app.exe" });
  fs.rmSync(app, { recursive: true });
  fs.writeFileSync(app, "");
  const server = await ollama(t, { models: ["qwen3.5:4b", "nomic-embed-text:v1.5"] });
  const procs = processList(path.join(path.dirname(app), "ollama app.exe").toUpperCase(), "C:\\Windows\\explorer.exe");
  const { calls, run } = runner({ "unins000.exe": () => { fs.rmSync(app); return { code: 0 }; } });

  const result = await removeEverything({ home: dir, vault, platform: "win32", runner: run, list: procs.list, base: server.base });
  assert.deepEqual(result, { problems: [], left: null });
  assert.deepEqual(calls[0], ["unins000.exe", ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]]);
  assert.deepEqual(server.deleted, ["qwen3.5:4b", "nomic-embed-text:v1.5"]);

  const busy = installed(t, { app: "Programs/Ollama/ollama app.exe", models: [] });
  const another = processList("D:\\Tools\\Ollama\\ollama.exe");
  const quiet = runner();
  const kept = await removeEverything({ home: busy.dir, vault: busy.vault, platform: "win32", runner: quiet.run, list: another.list });
  assert.deepEqual(kept.problems, ["Ollama is still installed: another Ollama is running (D:\\Tools\\Ollama\\ollama.exe): quit it, then remove Nuru again"]);
  assert.deepEqual(quiet.calls, [], "its uninstaller, which stops Ollama by name, isn't run");

  const again = installed(t, { app: "Programs/Ollama/ollama app.exe", models: [] });
  const failing = runner({ "unins000.exe": () => ({ code: 5 }) });
  assert.deepEqual((await removeEverything({ home: again.dir, vault: again.vault, platform: "win32", runner: failing.run, list: () => [] })).problems,
    ["Ollama is still installed: its uninstaller failed (exit 5)"]);
  const lingering = installed(t, { app: "Programs/Ollama/ollama app.exe", models: [] });
  assert.match((await removeEverything({ home: lingering.dir, vault: lingering.vault, platform: "win32", runner: runner().run, list: () => [],
    waitMs: 600 })).problems[0], /^Ollama is still installed: .*ollama app\.exe is still there$/);
});

test("what's Nuru's and still there stays recorded, so removing Nuru again can finish; the rest of ~/.nuru still goes", async (t) => {
  const { dir, vault, app } = installed(t);
  const server = await ollama(t, { models: ["nomic-embed-text:v1.5"] });
  const stuck = processList(`${app}/Contents/MacOS/Ollama`);
  const result = await removeEverything({ home: dir, vault, platform: "darwin", list: stuck.list, kill: () => {}, base: server.base, waitMs: 600 });
  assert.deepEqual(result.problems, ["Ollama is still installed: it didn't quit"]);
  assert.deepEqual(result.left, { ollama: app, models: [] }, "both models went: one deleted, one already gone");
  assert.deepEqual(fs.readdirSync(path.join(dir, ".nuru")), ["added.json"]);
  assert.deepEqual(added(dir), { ollama: app, models: [] });
  assert.ok(fs.existsSync(app) && !fs.existsSync(path.join(vault, ".nuru")));

  stuck.running = [];
  assert.deepEqual(await removeEverything({ home: dir, vault, platform: "darwin", list: stuck.list, kill: stuck.kill, base: server.base }),
    { problems: [], left: null }, "again, it finishes");
  assert.ok(!fs.existsSync(app) && !fs.existsSync(path.join(dir, ".nuru")));
});

test("an Ollama Nuru installed but stopped is started to delete the models; with no Ollama to ask, the models stay recorded", async (t) => {
  const { dir, vault, app } = installed(t);
  const server = await ollama(t, { models: ["qwen3.5:4b", "nomic-embed-text:v1.5"] });
  server.running = false;
  const started = [];
  const startOllama = async (where) => { started.push(where); server.running = true; };
  assert.deepEqual(await removeEverything({ home: dir, vault, platform: "darwin", list: () => [], base: server.base, startOllama }),
    { problems: [], left: null });
  assert.deepEqual([started, server.deleted.length], [[app], 2]);

  const theirs = installed(t, { app: null });
  const result = await removeEverything({ home: theirs.dir, vault: theirs.vault, platform: "darwin", list: () => [], base: "http://127.0.0.1:1" });
  assert.deepEqual(result.problems, ["qwen3.5:4b and nomic-embed-text:v1.5 are still in Ollama: Ollama isn't running: open it, then remove Nuru again"]);
  assert.deepEqual(added(theirs.dir), { ollama: null, models: ["qwen3.5:4b", "nomic-embed-text:v1.5"] });

  const one = installed(t, { app: null, models: ["qwen3.5:4b", "nomic-embed-text:v1.5"] });
  const locked = await ollama(t, { failing: true });
  const failed = await removeEverything({ home: one.dir, vault: one.vault, platform: "darwin", list: () => [], base: locked.base });
  assert.deepEqual(failed.problems.map((p) => p.split(":")[0]), ["qwen3.5", "nomic-embed-text"]);
  assert.match(failed.problems[0], /^qwen3\.5:4b is still in Ollama: Ollama answered 500/);
  assert.deepEqual(added(one.dir).models, ["qwen3.5:4b", "nomic-embed-text:v1.5"], "each model that failed stays recorded");
});

test("the process list names each running program by its full path", () => {
  const mine = processes().find((p) => p.pid === process.pid);
  assert.ok(mine, "this test's own process is listed");
  if (process.platform !== "linux") assert.ok(path.isAbsolute(mine.command), mine.command);
});

test("the Ollama Nuru installed is stopped with a real signal; a folder that can't be deleted is said", async (t) => {
  const { dir, vault, app } = installed(t, { models: [] });
  const child = childProcess.spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });
  t.after(() => child.kill());
  await new Promise((resolve) => child.once("spawn", resolve));
  const alive = () => child.exitCode === null && child.signalCode === null;
  const list = () => (alive() ? [{ pid: child.pid, command: `${app}/Contents/MacOS/Ollama` }] : []);
  const rm = fs.rmSync;
  fs.rmSync = (target, options) => {
    if (target === path.join(vault, ".nuru")) throw Object.assign(new Error("EBUSY: resource busy or locked"), { code: "EBUSY" });
    return rm(target, options);
  };
  let result;
  try {
    result = await removeEverything({ home: dir, vault, platform: "darwin", list });
  } finally {
    fs.rmSync = rm;
  }
  assert.ok(!alive(), "stopped");
  assert.deepEqual(result, { problems: [`${path.join(vault, ".nuru")} is still there: EBUSY: resource busy or locked`], left: null });
  assert.ok(!fs.existsSync(app));
});
