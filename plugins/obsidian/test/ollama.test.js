"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const os = require("node:os");

const { CHAT_MODEL, EMBEDDING_MODEL, atLeast, check, findApp, install, pull, start } = require("../lib/ollama");
const { zip } = require("./helpers/archives");

const POSIX = process.platform !== "win32";

const ROOT = path.join(__dirname, "..", "..", "..");

// A stand-in Ollama on 127.0.0.1: its version, its models, and /api/pull answering with `pullLines`
// (streamed one per line, then `tail` as is) or `pullStatus`.
async function ollama(t, { models = [], pullLines = [], pullStatus = 200, tail = "", stall = false } = {}) {
  const pulls = [];
  const srv = http.createServer((request, response) => {
    if (stall) return undefined;
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (request.url === "/api/version") return response.end(JSON.stringify({ version: "0.40.4" }));
      if (request.url === "/api/tags") return response.end(JSON.stringify({ models: models.map((name) => ({ name })) }));
      pulls.push(JSON.parse(body));
      response.writeHead(pullStatus);
      for (const line of pullLines) response.write(`${JSON.stringify(line)}\n`);
      return response.end(pullStatus === 200 ? tail : '{"error":"something broke"}');
    });
    return undefined;
  });
  await new Promise((resolve) => srv.listen(0, "127.0.0.1", resolve));
  t.after(() => { srv.closeAllConnections(); srv.close(); });
  return { base: `http://127.0.0.1:${srv.address().port}`, pulls };
}

test("check says which needed models Ollama lacks, taking a bare name as :latest", async (t) => {
  const { base } = await ollama(t, { models: ["granite4.2:3b", "llama3:latest"] });
  assert.deepEqual(await check(["granite4.2:3b", "llama3", "nomic-embed-text:v1.5"], { base }),
    { running: true, version: "0.40.4", missing: ["nomic-embed-text:v1.5"] });
});

test("check finds Ollama not running when nothing answers, or it doesn't answer in time", async (t) => {
  assert.deepEqual(await check(["a"], { base: "http://127.0.0.1:1" }), { running: false, version: null, missing: ["a"] });
  const { base } = await ollama(t, { stall: true });
  assert.deepEqual(await check(["a"], { base, timeoutMs: 100 }), { running: false, version: null, missing: ["a"] });
});

test("pull streams how many bytes of each part Ollama has, and 0 of 0 while it does something else", async (t) => {
  const { base, pulls } = await ollama(t, { pullLines: [{ status: "pulling manifest" },
    { status: "pulling 1a2b", total: 200, completed: 50 }, { status: "pulling 1a2b", total: 200 }, { status: "success" }] });
  const progress = [];
  await pull("granite4.2:3b", (...bytes) => progress.push(bytes), { base });
  assert.deepEqual(pulls, [{ model: "granite4.2:3b", stream: true }]);
  assert.deepEqual(progress, [[0, 0], [50, 200], [0, 200], [0, 0]]);
});

test("a pull Ollama reports an error in, or answers with a failure, is refused", async (t) => {
  const failing = await ollama(t, { pullLines: [{ status: "pulling manifest" }, { error: "file does not exist" }] });
  await assert.rejects(pull("nope", undefined, { base: failing.base }), /file does not exist/);
  const broken = await ollama(t, { pullStatus: 500 });
  await assert.rejects(pull("nope", () => {}, { base: broken.base }), /Ollama answered 500: {"error":"something broke"}/);
  await assert.rejects(pull("nope", () => {}, { base: "http://127.0.0.1:1" }), /ECONNREFUSED/);
});

test("a pull is done only on Ollama's success line, read even without its final newline", async (t) => {
  const progress = [];
  const unterminated = await ollama(t, { pullLines: [{ status: "pulling manifest" }], tail: '{"status":"success"}' });
  await pull("m", (...bytes) => progress.push(bytes), { base: unterminated.base });
  assert.deepEqual(progress, [[0, 0], [0, 0]], "both lines, the last without its newline");

  const diskFull = await ollama(t, { pullLines: [{ status: "pulling 1a2b", total: 10, completed: 5 }], tail: '{"error":"disk full"}' });
  await assert.rejects(pull("m", () => {}, { base: diskFull.base }), /^Error: disk full$/);
  const cut = await ollama(t, { pullLines: [{ status: "pulling 1a2b", total: 10, completed: 5 }] });
  await assert.rejects(pull("m", () => {}, { base: cut.base }), /downloading m stopped before Ollama finished it/);
  const garbled = await ollama(t, { pullLines: [{ status: "pulling manifest" }], tail: '{"status":' });
  await assert.rejects(pull("m", () => {}, { base: garbled.base }), SyntaxError);
});

test("a stalled pull or a failing tags list is an error", async (t) => {
  const stalled = await ollama(t, { stall: true });
  await assert.rejects(pull("x", () => {}, { base: stalled.base, timeoutMs: 100 }), /Ollama didn't answer/);
  const srv = http.createServer((request, response) => {
    if (request.url === "/api/version") return response.end('{"version":"1"}');
    response.writeHead(500);
    return response.end();
  });
  await new Promise((resolve) => srv.listen(0, "127.0.0.1", resolve));
  t.after(() => srv.close());
  await assert.rejects(check(["a"], { base: `http://127.0.0.1:${srv.address().port}` }), /^Error: Ollama answered 500$/);
});

test("the plugin's default models are Nuru's own", () => {
  const session = fs.readFileSync(path.join(ROOT, "nuru", "src", "core", "session.py"), "utf8");
  assert.ok(session.includes(`BASE_MODEL = os.environ.get("NURU_BASE_MODEL", "${CHAT_MODEL}")`));
  const semantic = fs.readFileSync(path.join(ROOT, "servers", "obsidian", "src", "obsidian_mcp", "semantic.py"), "utf8");
  assert.ok(semantic.includes(`DEFAULT_MODEL = "${EMBEDDING_MODEL}"`));
});

const PIN = { version: "0.40.4", url: "https://github.com/ollama/ollama/releases/download/v0.40.4/",
  systems: { "win32-x64": { asset: "OllamaSetup.exe", sha256: "9095bdb859308b62acf04036ffd4adfe366d7f737d276eb6c46ae434f3816c9b" }, "darwin-arm64": { asset: "Ollama-darwin.zip", sha256: "a".repeat(64) } } };

function home(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "nuru ollama home "));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

// Stands in for child_process.spawn; with `error`, the child reports it the way a missing program does.
const spawned = (error = null) => {
  const calls = [];
  const spawn = (command, args, options) => {
    calls.push([command, args, options.shell]);
    return { unref() {}, on(event, handler) { if (event === "error" && error) setTimeout(() => handler(error), 10); } };
  };
  return { calls, spawn };
};

test("atLeast compares Ollama versions number by number, and never asks for a downgrade", () => {
  assert.equal(atLeast("0.40.4", "0.40.4"), true);
  assert.equal(atLeast("0.40.11", "0.40.4"), true, "11 > 4, not '11' < '4'");
  assert.equal(atLeast("1.0.0", "0.40.4"), true);
  assert.equal(atLeast("0.38.2", "0.40.4"), false);
  assert.equal(atLeast("0.40.3-rc1", "0.40.4"), false);
  assert.equal(atLeast("0.40.4-rc1", "0.40.4"), false, "a pre-release comes before its release");
  assert.equal(atLeast("0.40.4-rc2", "0.40.4-rc1"), true);
  assert.equal(atLeast("0.40.5-rc1", "0.40.4"), true);
  assert.equal(atLeast("0.0.0", "0.40.4"), false);
});

test("findApp finds Ollama where its installers put it, or nothing", (t) => {
  const dir = home(t);
  assert.equal(findApp({ platform: "win32", home: dir, env: { LOCALAPPDATA: path.join(dir, "Local") } }), null);
  const exe = path.join(dir, "Local", "Programs", "Ollama", "ollama app.exe");
  fs.mkdirSync(path.dirname(exe), { recursive: true });
  fs.writeFileSync(exe, "");
  assert.equal(findApp({ platform: "win32", home: dir, env: { LOCALAPPDATA: path.join(dir, "Local") } }), exe);
  fs.mkdirSync(path.join(dir, "AppData", "Local", "Programs", "Ollama"), { recursive: true });
  fs.writeFileSync(path.join(dir, "AppData", "Local", "Programs", "Ollama", "ollama app.exe"), "");
  assert.equal(findApp({ platform: "win32", home: dir, env: {} }), path.join(dir, "AppData", "Local", "Programs", "Ollama", "ollama app.exe"));

  const mine = path.join(dir, "Applications", "Ollama.app");
  fs.mkdirSync(mine, { recursive: true });
  const system = fs.existsSync("/Applications/Ollama.app") ? "/Applications/Ollama.app" : mine;
  assert.equal(findApp({ platform: "darwin", home: dir }), system);
});

test("on Windows, Ollama's installer is downloaded to a file, run silently without a shell, removed, and Ollama awaited", async (t) => {
  const dir = home(t);
  const { base } = await ollama(t);
  const fetched = [];
  const fetch = async (url, options) => { fetched.push([url, options.sha256, options.maxBytes]); fs.writeFileSync(options.to, "exe"); return options.to; };
  const ran = [];
  const runner = async (command, args) => { ran.push([command, args, fs.existsSync(command)]); return { code: 0, output: "" }; };
  const steps = [];

  const version = await install(PIN, "win32-x64", { home: dir, fetch, runner, step: (s) => steps.push(s), platform: "win32", base });

  assert.equal(version, "0.40.4");
  const installer = path.join(dir, ".nuru", "downloads", "OllamaSetup.exe");
  assert.deepEqual(fetched, [[`${PIN.url}OllamaSetup.exe`, PIN.systems["win32-x64"].sha256, 2 * 1024 ** 3]]);
  assert.deepEqual(ran, [[installer, ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], true]]);
  assert.ok(!fs.existsSync(installer), "the installer is removed");
  assert.deepEqual(steps, ["Downloading Ollama 0.40.4…", "Installing Ollama 0.40.4…", "Waiting for Ollama to start…"]);

  await assert.rejects(install(PIN, "win32-x64", { home: dir, fetch, runner: async () => ({ code: 1 }), platform: "win32", base }),
    /Ollama's installer failed \(exit 1\)/);
  assert.ok(!fs.existsSync(installer));
});

test("on macOS, Ollama.app is unpacked with its links into ~/Applications and opened", { skip: !POSIX && "symlinks" }, async (t) => {
  const dir = home(t);
  const { base } = await ollama(t);
  const archive = zip([{ name: "Ollama.app/Contents/MacOS/Ollama", data: "app", mode: 0o100755 },
    { name: "Ollama.app/Contents/Resources/libggml.dylib", data: "libggml.0.dylib", mode: 0o120777 },
    { name: "Ollama.app/Contents/Resources/libggml.0.dylib", data: "lib" }]);
  const { calls, spawn } = spawned();

  const version = await install(PIN, "darwin-arm64", { home: dir, fetch: async () => archive, platform: "darwin", spawn, base });

  const app = path.join(dir, "Applications", "Ollama.app");
  assert.equal(version, "0.40.4");
  assert.equal(fs.readFileSync(path.join(app, "Contents", "Resources", "libggml.dylib"), "utf8"), "lib");
  assert.equal(fs.statSync(path.join(app, "Contents", "MacOS", "Ollama")).mode & 0o777, 0o755);
  assert.deepEqual(calls, [["/usr/bin/open", [app], false]]);
  assert.deepEqual(fs.readdirSync(path.join(dir, "Applications")), ["Ollama.app"], "no staging folder left");

  await assert.rejects(install(PIN, "darwin-arm64", { home: dir, fetch: async () => archive, platform: "darwin", spawn, base }),
    /Ollama\.app is already there: open it, or remove it and try again/);
  const broken = zip([{ name: "Ollama.app/x", data: "/etc", mode: 0o120777 }]);
  fs.rmSync(app, { recursive: true });
  await assert.rejects(install(PIN, "darwin-arm64", { home: dir, fetch: async () => broken, platform: "darwin", spawn, base }), /links outside/);
  assert.deepEqual(fs.readdirSync(path.join(dir, "Applications")), [], "a refused app leaves nothing");
});

test("start opens an installed Ollama and waits for it; one that never answers is reported", async (t) => {
  const { base } = await ollama(t);
  const windows = spawned();
  assert.equal(await start("C:/Ollama/ollama app.exe", { platform: "win32", spawn: windows.spawn, base }), "0.40.4");
  assert.deepEqual(windows.calls, [["C:/Ollama/ollama app.exe", [], false]]);
  const mac = spawned();
  await assert.rejects(start("/Applications/Ollama.app", { platform: "darwin", spawn: mac.spawn, base: "http://127.0.0.1:1", waitMs: 1500 }),
    /Ollama didn't start: open it from your applications, then click Check\./);
  assert.deepEqual(mac.calls, [["/usr/bin/open", ["/Applications/Ollama.app"], false]]);
});

test("a computer with no Ollama release in the pins is sent to Ollama's site", async () => {
  await assert.rejects(install(PIN, "linux-x64", {}), /Nuru can't install Ollama on this computer \(linux-x64\): get it from https:\/\/ollama\.com\/download/);
});

test("an installer that finishes but leaves no Ollama answering is reported", async (t) => {
  const dir = home(t);
  const fetch = async (url, options) => { fs.writeFileSync(options.to, "exe"); return options.to; };
  await assert.rejects(install(PIN, "win32-x64", { home: dir, fetch, runner: async () => ({ code: 0 }), platform: "win32",
    base: "http://127.0.0.1:1", waitMs: 1500 }), /Ollama didn't start: open it from your applications, then click Check\./);
});

test("an installer whose bytes changed on disk after the download isn't run", async (t) => {
  const dir = home(t);
  const ran = [];
  const fetch = async (url, options) => { fs.writeFileSync(options.to, "swapped"); return options.to; };
  await assert.rejects(install(PIN, "win32-x64", { home: dir, fetch, runner: async (c) => { ran.push(c); return { code: 0 }; }, platform: "win32" }),
    /Ollama's installer changed after it was downloaded/);
  assert.deepEqual(ran, []);
  assert.deepEqual(fs.readdirSync(path.join(dir, ".nuru", "downloads")), []);
});

test("Ollama installs one at a time with Nuru's setup: another window holding the setup lock stops it", async (t) => {
  const dir = home(t);
  const locks = path.join(dir, ".nuru", "setup-locks");
  fs.mkdirSync(locks, { recursive: true });
  fs.writeFileSync(path.join(locks, `${process.pid}-other-window`), "");
  let fetched = false;
  await assert.rejects(install(PIN, "win32-x64", { home: dir, fetch: async () => { fetched = true; }, platform: "win32" }),
    /already being set up, in another Obsidian window/);
  assert.equal(fetched, false);
});

test("an app that can't be opened is reported at once, not after the wait", async (t) => {
  const { calls, spawn } = spawned(Object.assign(new Error("spawn ENOENT"), { code: "ENOENT" }));
  const began = Date.now();
  await assert.rejects(start("C:/gone/ollama app.exe", { platform: "win32", spawn, base: "http://127.0.0.1:1", waitMs: 60000 }),
    /Couldn't open Ollama: spawn ENOENT/);
  assert.ok(Date.now() - began < 10000);
  assert.equal(calls.length, 1);
});
