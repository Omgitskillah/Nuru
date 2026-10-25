"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const POSIX = process.platform !== "win32";
const { added, processes, remember, removeEverything } = require("../lib/remove");

function home(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "nuru remove home "));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

// A stand-in Ollama: answers while `running`, records the models deleted, and has only `models`; with
// `failing`, a delete fails.
async function ollama(t, { models = [], failing = false } = {}) {
  const state = { running: true, deleted: [], failing };
  const srv = http.createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (!state.running) return response.writeHead(503).end();
      if (request.url === "/api/version") return response.end(JSON.stringify({ version: "0.40.4" }));
      if (request.url === "/api/tags") return response.end(JSON.stringify({ models: [] }));
      const { model } = JSON.parse(body);
      if (state.failing) return response.writeHead(500).end(JSON.stringify({ error: "database is locked" }));
      if (!models.includes(model)) return response.writeHead(404).end(JSON.stringify({ error: "not found" }));
      state.deleted.push(model);
      return response.end();
    });
  });
  await new Promise((resolve) => srv.listen(0, "127.0.0.1", resolve));
  t.after(() => { srv.closeAllConnections(); srv.close(); });
  return Object.assign(state, { base: `http://127.0.0.1:${srv.address().port}` });
}

const PLIST = '<?xml version="1.0"?><plist><dict><key>CFBundleIdentifier</key><string>com.electron.ollama</string></dict></plist>';

// A home with Nuru installed and, unless `app` is null, an Ollama.app Nuru added where it installs it,
// and a vault with notes and Nuru's data.
function installed(t, { app = true, models = ["qwen3.5:4b", "nomic-embed-text:v1.5"] } = {}) {
  const dir = home(t);
  fs.mkdirSync(path.join(dir, ".nuru", "app", "1.0.2", "1"), { recursive: true });
  fs.writeFileSync(path.join(dir, ".nuru", "install.json"), "{}");
  const appPath = path.join(dir, "Applications", "Ollama.app");
  if (app) {
    fs.mkdirSync(path.join(appPath, "Contents"), { recursive: true });
    fs.writeFileSync(path.join(appPath, "Contents", "Info.plist"), PLIST);
    remember(dir, { app: appPath });
  }
  for (const model of models) remember(dir, { model });
  const vault = path.join(dir, "Vault");
  fs.mkdirSync(path.join(vault, ".nuru", "db"), { recursive: true });
  fs.writeFileSync(path.join(vault, "Note.md"), "# mine\n");
  return { dir, vault, app: appPath };
}

// Stands in for the process list: `running` are the programs' paths; kill ends one.
function processList(...running) {
  const state = { running: running.map((command, i) => ({ pid: 100 + i, command })), killed: [] };
  return Object.assign(state, { list: () => state.running.map((p) => ({ ...p })),
    kill: (pid) => { state.killed.push(pid); state.running = state.running.filter((p) => p.pid !== pid); } });
}

const gone = (...paths) => paths.every((p) => !fs.existsSync(p));

test("Nuru records what it added: the Ollama it installed and each model once; nothing recorded is nothing", (t) => {
  const dir = home(t);
  assert.deepEqual(added(dir), { ollama: null, models: [] });
  remember(dir, { model: "a" });
  remember(dir, { model: "a" });
  remember(dir, { app: "/x/Ollama.app" });
  assert.deepEqual(added(dir), { ollama: "/x/Ollama.app", models: ["a"] });
  assert.deepEqual(fs.readdirSync(path.join(dir, ".nuru")), ["added.json"], "no partial record left");
  fs.writeFileSync(path.join(dir, ".nuru", "added.json"), "{broken");
  assert.deepEqual(added(dir), { ollama: null, models: [] });
});

test("on macOS everything Nuru added goes: its models, only the Ollama.app it installed, ~/.nuru and the vault's .nuru; the notes stay", async (t) => {
  const { dir, vault, app } = installed(t);
  const server = await ollama(t, { models: ["qwen3.5:4b"] }); // the embedding model is gone already
  const procs = processList(`${app}/Contents/MacOS/Ollama`, `${app}/Contents/Resources/ollama`, "/Applications/Ollama.app/Contents/Resources/ollama",
    `${app}-copy/Contents/MacOS/Ollama`);
  const said = [];

  const result = await removeEverything({ home: dir, vault, platform: "darwin", list: procs.list, kill: procs.kill, base: server.base,
    say: (s) => said.push(s) });

  assert.deepEqual(result, { problems: [], left: null, manual: [] });
  assert.deepEqual(server.deleted, ["qwen3.5:4b"]);
  assert.deepEqual(procs.killed, [100, 101], "only processes running from inside the app Nuru installed");
  assert.ok(gone(app, path.join(dir, ".nuru"), path.join(vault, ".nuru")));
  assert.equal(fs.readFileSync(path.join(vault, "Note.md"), "utf8"), "# mine\n");
  assert.deepEqual(said, ["Deleting Nuru's models from Ollama…", "Removing Ollama…", "Deleting Nuru's install and this vault's Nuru data…"]);
});

test("an app the record names is never stopped or deleted unless it's the Ollama.app Nuru installs", async (t) => {
  const elsewhere = installed(t, { app: null, models: [] });
  const unrelated = path.join(elsewhere.dir, "Unrelated-project");
  fs.mkdirSync(unrelated);
  fs.writeFileSync(path.join(unrelated, "keep.txt"), "mine");
  remember(elsewhere.dir, { app: unrelated });
  const procs = processList(`${unrelated}/server`);
  const result = await removeEverything({ home: elsewhere.dir, vault: elsewhere.vault, platform: "darwin", list: procs.list, kill: procs.kill });
  assert.deepEqual(result.problems, [`Nuru's record names ${unrelated} as its Ollama, but that isn't the Ollama Nuru installs, so it's left alone`]);
  assert.equal(fs.readFileSync(path.join(unrelated, "keep.txt"), "utf8"), "mine");
  assert.deepEqual(procs.killed, []);

  for (const make of [
    (app) => { fs.mkdirSync(app); }, // no Info.plist
    (app) => { fs.mkdirSync(path.join(app, "Contents"), { recursive: true }); fs.writeFileSync(path.join(app, "Contents", "Info.plist"), "<string>com.other</string>"); },
    ...(POSIX ? [(app) => { fs.mkdirSync(`${app}-real/Contents`, { recursive: true }); fs.writeFileSync(`${app}-real/Contents/Info.plist`, PLIST);
      fs.symlinkSync(`${app}-real`, app); }] : []),
  ]) {
    const { dir, vault } = installed(t, { app: null, models: [] });
    const app = path.join(dir, "Applications", "Ollama.app");
    fs.mkdirSync(path.dirname(app), { recursive: true });
    make(app);
    remember(dir, { app });
    const { problems } = await removeEverything({ home: dir, vault, platform: "darwin", list: () => [] });
    assert.match(problems[0], /isn't the Ollama Nuru installs, so it's left alone$/);
    assert.ok(fs.existsSync(app) && (!POSIX || !fs.existsSync(`${app}-real`) || fs.existsSync(`${app}-real/Contents/Info.plist`)));
  }
});

test("on Windows the Ollama Nuru installed is left for the user to uninstall: its uninstaller stops every Ollama", async (t) => {
  const { dir, vault } = installed(t, { app: null });
  remember(dir, { app: "C:\\Users\\me\\AppData\\Local\\Programs\\Ollama\\ollama app.exe" });
  const server = await ollama(t, { models: ["qwen3.5:4b", "nomic-embed-text:v1.5"] });
  const procs = processList("C:\\Users\\me\\AppData\\Local\\Programs\\Ollama\\ollama app.exe");
  const result = await removeEverything({ home: dir, vault, platform: "win32", list: procs.list, kill: procs.kill, base: server.base });
  assert.deepEqual(result, { problems: [], left: null, manual: ["Uninstall Ollama, which Nuru installed, in Windows Settings → Apps → Installed apps"] });
  assert.deepEqual(server.deleted, ["qwen3.5:4b", "nomic-embed-text:v1.5"], "Nuru's models still go");
  assert.deepEqual(procs.killed, []);
  assert.ok(gone(path.join(dir, ".nuru")));
});

test("while a model can't be deleted, the Ollama holding it stays too, both recorded, so removing Nuru again can finish", async (t) => {
  const { dir, vault, app } = installed(t);
  const locked = await ollama(t, { models: ["qwen3.5:4b", "nomic-embed-text:v1.5"], failing: true });
  const procs = processList(`${app}/Contents/MacOS/Ollama`);
  const result = await removeEverything({ home: dir, vault, platform: "darwin", list: procs.list, kill: procs.kill, base: locked.base });
  assert.deepEqual(result.problems.map((p) => p.split(" is still")[0]), ["qwen3.5:4b", "nomic-embed-text:v1.5"]);
  assert.match(result.problems[0], /Ollama answered 500/);
  assert.deepEqual(result.left, { ollama: app, models: ["qwen3.5:4b", "nomic-embed-text:v1.5"] });
  assert.deepEqual(procs.killed, [], "Ollama is left running with Nuru's models");
  assert.deepEqual(fs.readdirSync(path.join(dir, ".nuru")), ["added.json"], "the rest of ~/.nuru goes");
  assert.deepEqual(added(dir), result.left);
  assert.ok(fs.existsSync(app) && gone(path.join(vault, ".nuru")));

  locked.failing = false;
  assert.deepEqual(await removeEverything({ home: dir, vault, platform: "darwin", list: procs.list, kill: procs.kill, base: locked.base }),
    { problems: [], left: null, manual: [] }, "again, it finishes");
  assert.ok(gone(app, path.join(dir, ".nuru")));
});

test("an Ollama that won't quit stays recorded; a stopped one Nuru installed is started to delete the models", async (t) => {
  const { dir, vault, app } = installed(t, { models: [] });
  const stuck = processList(`${app}/Contents/MacOS/Ollama`);
  const result = await removeEverything({ home: dir, vault, platform: "darwin", list: stuck.list, kill: () => {}, waitMs: 600 });
  assert.deepEqual(result.problems, ["Ollama is still installed: it didn't quit"]);
  assert.deepEqual(added(dir), { ollama: app, models: [] });

  const stopped = installed(t);
  const server = await ollama(t, { models: ["qwen3.5:4b", "nomic-embed-text:v1.5"] });
  server.running = false;
  const started = [];
  const startOllama = async (where) => { started.push(where); server.running = true; };
  assert.deepEqual((await removeEverything({ home: stopped.dir, vault: stopped.vault, platform: "darwin", list: () => [], base: server.base,
    startOllama })).problems, []);
  assert.deepEqual([started, server.deleted.length], [[stopped.app], 2]);

  const theirs = installed(t, { app: null });
  const none = await removeEverything({ home: theirs.dir, vault: theirs.vault, platform: "darwin", list: () => [], base: "http://127.0.0.1:1" });
  assert.deepEqual(none.problems, ["qwen3.5:4b and nomic-embed-text:v1.5 are still in Ollama: Ollama isn't running: open it, then remove Nuru again"]);
  assert.deepEqual(added(theirs.dir), { ollama: null, models: ["qwen3.5:4b", "nomic-embed-text:v1.5"] });
});

test("the record is kept even when deleting the rest of ~/.nuru fails; a vault folder that can't go is said", async (t) => {
  const { dir, vault, app } = installed(t, { models: [] });
  const rm = fs.rmSync;
  fs.rmSync = (target, options) => {
    if (target === path.join(dir, ".nuru", "app") || target === path.join(vault, ".nuru")) {
      throw Object.assign(new Error("EBUSY: resource busy or locked"), { code: "EBUSY" });
    }
    return rm(target, options);
  };
  let result;
  try {
    result = await removeEverything({ home: dir, vault, platform: "darwin", list: () => [{ pid: 1, command: `${app}/x` }], kill: () => {}, waitMs: 600 });
  } finally {
    fs.rmSync = rm;
  }
  assert.deepEqual(result.problems, ["Ollama is still installed: it didn't quit", `${path.join(dir, ".nuru")} is still there: EBUSY: resource busy or locked`,
    `${path.join(vault, ".nuru")} is still there: EBUSY: resource busy or locked`]);
  assert.deepEqual(added(dir), { ollama: app, models: [] });

  const plain = installed(t, { app: null, models: [] });
  fs.rmSync = (target, options) => {
    if (target === path.join(plain.dir, ".nuru")) throw Object.assign(new Error("EPERM"), { code: "EPERM" });
    return rm(target, options);
  };
  try {
    result = await removeEverything({ home: plain.dir, vault: plain.vault, platform: "darwin", list: () => [] });
  } finally {
    fs.rmSync = rm;
  }
  assert.deepEqual(result.problems, [`${path.join(plain.dir, ".nuru")} is still there: EPERM`]);
});

test("the Ollama Nuru installed is stopped with a real signal, and the process list names programs by full path", async (t) => {
  const { dir, vault, app } = installed(t, { models: [] });
  const child = childProcess.spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });
  t.after(() => child.kill());
  await new Promise((resolve) => child.once("spawn", resolve));
  const alive = () => child.exitCode === null && child.signalCode === null;
  const list = () => (alive() ? [{ pid: child.pid, command: `${app}/Contents/MacOS/Ollama` }] : []);
  assert.deepEqual((await removeEverything({ home: dir, vault, platform: "darwin", list })).problems, []);
  assert.ok(!alive() && gone(app));
  if (POSIX) {
    const mine = processes().find((p) => p.pid === process.pid);
    assert.ok(mine, "this test's own process is listed");
  }
});
