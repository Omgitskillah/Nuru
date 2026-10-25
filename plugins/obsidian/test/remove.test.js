"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const { added, remember, removeEverything } = require("../lib/remove");

function home(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "nuru remove home "));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

// A stand-in Ollama: answers while `running`, records the models deleted, and has only `models`.
async function ollama(t, { models = [] } = {}) {
  const state = { running: true, deleted: [] };
  const srv = http.createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (!state.running) return response.writeHead(503).end();
      if (request.url === "/api/version") return response.end(JSON.stringify({ version: "0.40.4" }));
      if (request.url === "/api/tags") return response.end(JSON.stringify({ models: [] }));
      const { model } = JSON.parse(body);
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

test("on macOS everything goes: Nuru's models, the Ollama it installed, uv's cache, ~/.nuru and the vault's .nuru; the notes stay", async (t) => {
  const { dir, vault, uv, app } = installed(t);
  const server = await ollama(t, { models: ["qwen3.5:4b"] }); // the embedding model is gone already
  const { calls, run } = runner({ pkill: () => { server.running = false; return { code: 0 }; } });
  const said = [];

  const problems = await removeEverything({ home: dir, vault, platform: "darwin", runner: run, base: server.base, say: (s) => said.push(s) });

  assert.deepEqual(problems, []);
  assert.deepEqual(server.deleted, ["qwen3.5:4b"]);
  assert.deepEqual(calls, [["pkill", ["-x", "Ollama"]], ["pkill", ["-x", "ollama"]], [path.basename(uv), ["cache", "clean"]]]);
  assert.ok(!fs.existsSync(app) && !fs.existsSync(path.join(dir, ".nuru")) && !fs.existsSync(path.join(vault, ".nuru")));
  assert.equal(fs.readFileSync(path.join(vault, "Note.md"), "utf8"), "# mine\n");
  assert.deepEqual(said, ["Deleting Nuru's models from Ollama…", "Removing Ollama…", "Clearing uv's download cache…",
    "Deleting Nuru's install and this vault's Nuru data…"]);
});

test("on Windows the Ollama Nuru installed goes with its own uninstaller, run silently", async (t) => {
  const { dir, vault, app } = installed(t, { app: "Programs/Ollama/ollama app.exe" });
  fs.rmSync(app, { recursive: true });
  fs.writeFileSync(app, "");
  const server = await ollama(t, { models: ["qwen3.5:4b", "nomic-embed-text:v1.5"] });
  const { calls, run } = runner({ "unins000.exe": () => { fs.rmSync(app); return { code: 0 }; } });

  assert.deepEqual(await removeEverything({ home: dir, vault, platform: "win32", runner: run, base: server.base }), []);
  assert.deepEqual(calls[0], ["unins000.exe", ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]]);
  assert.deepEqual(server.deleted, ["qwen3.5:4b", "nomic-embed-text:v1.5"]);

  const again = installed(t, { app: "Programs/Ollama/ollama app.exe", models: [] });
  const failing = runner({ "unins000.exe": () => ({ code: 5 }) });
  assert.deepEqual(await removeEverything({ home: again.dir, vault: again.vault, platform: "win32", runner: failing.run }),
    ["Ollama is still installed: its uninstaller failed (exit 5)"]);
  const lingering = installed(t, { app: "Programs/Ollama/ollama app.exe", models: [] });
  assert.match((await removeEverything({ home: lingering.dir, vault: lingering.vault, platform: "win32", runner: runner().run, waitMs: 600 }))[0],
    /^Ollama is still installed: .*ollama app\.exe is still there$/);
});

test("an Ollama Nuru installed but stopped is started to delete the models; one Nuru didn't install is left as it is", async (t) => {
  const { dir, vault, app } = installed(t);
  const server = await ollama(t, { models: ["qwen3.5:4b", "nomic-embed-text:v1.5"] });
  server.running = false;
  const started = [];
  const startOllama = async (where) => { started.push(where); server.running = true; };
  const { run } = runner({ pkill: () => { server.running = false; return { code: 0 }; } });
  assert.deepEqual(await removeEverything({ home: dir, vault, platform: "darwin", runner: run, base: server.base, startOllama }), []);
  assert.deepEqual([started, server.deleted.length], [[app], 2]);

  const theirs = installed(t, { app: null });
  const { calls, run: run2 } = runner({ uv: () => ({ code: 2, output: "error: cache is in use\n" }) });
  const problems = await removeEverything({ home: theirs.dir, vault: theirs.vault, platform: "darwin", runner: run2, base: "http://127.0.0.1:1" });
  assert.deepEqual(problems, ["Nuru's models are still in Ollama: Ollama isn't running: open it and delete them there, or with `ollama rm`",
    "uv's download cache is still there: error: cache is in use"]);
  assert.ok(!calls.some(([command]) => command === "pkill"), "an Ollama Nuru didn't install is never quit");
  assert.ok(!fs.existsSync(path.join(theirs.dir, ".nuru")), "the rest still goes");
});

test("an Ollama that won't quit stays, and is said; with no install there's no uv cache to clear", async (t) => {
  const { dir, vault, app } = installed(t, { models: [] });
  fs.rmSync(path.join(dir, ".nuru", "install.json"));
  const server = await ollama(t);
  const { calls, run } = runner();
  assert.deepEqual(await removeEverything({ home: dir, vault, platform: "darwin", runner: run, base: server.base, waitMs: 600 }),
    ["Ollama is still installed: it didn't quit"]);
  assert.ok(fs.existsSync(app));
  assert.deepEqual(calls.map(([command]) => command), ["pkill", "pkill"]);
});
