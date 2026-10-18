"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { EventEmitter } = require("node:events");

const { ConnectionProblem } = require("../lib/connection");
const { Launcher, LaunchProblem, logPath } = require("../lib/launcher");

function home({ installed = true, token = true, searchPath = "/Users/me/.local/bin:/Users/me/.cargo/bin:/usr/bin" } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-launch-"));
  const nuru = path.join(dir, "Nuru");
  const uv = path.join(dir, "uv");
  fs.mkdirSync(nuru);
  fs.writeFileSync(uv, "");
  if (installed) {
    fs.mkdirSync(path.join(dir, ".nuru"));
    fs.writeFileSync(path.join(dir, ".nuru", "install.json"), JSON.stringify({ nuru, uv, path: searchPath }));
  }
  if (token) {
    fs.mkdirSync(path.join(dir, ".nuru", "service", "clients"), { recursive: true });
    fs.writeFileSync(path.join(dir, ".nuru", "service", "clients", "obsidian.token"), "secret\n");
  }
  return { dir, nuru, uv };
}

function fakeChild() {
  const child = new EventEmitter();
  child.exitCode = null;
  child.signalCode = null;
  child.stdin = { ended: false, destroyed: false, end() { this.ended = true; } };
  return child;
}

test("it starts nuru-serve --managed for this vault, from the recorded Nuru folder, and waits until it answers", async () => {
  const { dir, nuru, uv } = home();
  const spawned = [];
  let tries = 0;
  const launcher = new Launcher({
    vaultPath: "/vaults/Here", port: 27125, home: dir, pollMs: 1,
    spawn: (command, args, options) => { spawned.push({ command, args, options }); return fakeChild(); },
    connect: async () => {
      tries += 1;
      if (tries < 3) throw new ConnectionProblem("no-service");
      return { client: "client", status: "status" };
    },
  });
  assert.deepEqual(await launcher.start(), { client: "client", status: "status" });
  assert.equal(spawned.length, 1);
  const [{ command, args, options }] = spawned;
  assert.equal(command, uv);
  assert.deepEqual(args, ["run", "--directory", nuru, "nuru-serve", "--managed", "--port", "27125"]);
  assert.equal(options.env.VAULT_PATH, "/vaults/Here");
  assert.equal(options.env.PATH, "/Users/me/.local/bin:/Users/me/.cargo/bin:/usr/bin", "the shell's PATH: uv and cargo are found");
  assert.equal(options.stdio[0], "pipe", "its stdin is the pipe that stops it");
  assert.ok(fs.existsSync(logPath(dir)));
  assert.ok(launcher.running());
  launcher.release();
  assert.ok(spawned.length === 1 && !launcher.running());
});

test("without install.json it says how to fix it, and never runs anything", async () => {
  const { dir } = home({ installed: false });
  const launcher = new Launcher({ vaultPath: "/v", port: 1, home: dir, spawn: () => assert.fail("spawned"),
    connect: async () => assert.fail("connected") });
  await assert.rejects(launcher.start(), (error) => error instanceof LaunchProblem && error.kind === "not-installed");
});

test("with no token yet it pairs first, the way `nuru-serve token add obsidian` does", async () => {
  const { dir, nuru, uv } = home({ token: false });
  const ran = [];
  const launcher = new Launcher({ vaultPath: "/v", port: 1, home: dir, pollMs: 1, spawn: () => fakeChild(),
    execFile: (command, args, options, done) => { ran.push([command, args]); done(null); },
    connect: async () => ({ client: 1, status: 2 }) });
  await launcher.start();
  assert.deepEqual(ran, [[uv, ["run", "--directory", nuru, "nuru-serve", "token", "add", "obsidian"]]]);
});

test("a service that exits on its way up is reported with the end of its log", async () => {
  const { dir } = home();
  const child = fakeChild();
  const launcher = new Launcher({ vaultPath: "/v", port: 1, home: dir, pollMs: 1,
    spawn: () => {
      fs.appendFileSync(logPath(dir), "nuru-serve: can't listen on 127.0.0.1:1: Address already in use\n");
      child.exitCode = 1;
      return child;
    },
    connect: async () => { throw new ConnectionProblem("no-service"); } });
  await assert.rejects(launcher.start(), (error) => error.kind === "exited" && /Address already in use/.test(error.message));
});

test("switching sends the stop key from managed.json, and a busy service is refused", async () => {
  const { dir } = home();
  fs.writeFileSync(path.join(dir, ".nuru", "service", "managed.json"), JSON.stringify({ pid: 1, port: 27125, stop_key: "key" }));
  const sent = [];
  let answer = 409;
  let up = true;
  const launcher = new Launcher({ vaultPath: "/v", port: 27125, home: dir, pollMs: 1, connect: async () => {},
    fetch: async (url, options = {}) => {
      if (url.endsWith("/v1/service/stop")) {
        sent.push([url, options.headers.Authorization, JSON.parse(options.body)]);
        if (answer === 202) up = false;
        return { status: answer };
      }
      if (!up) throw new TypeError("fetch failed");
      return { status: 200 };
    } });
  await assert.rejects(launcher.stopOther(), (error) => error.kind === "busy");
  answer = 202;
  await launcher.stopOther();
  assert.deepEqual(sent.at(-1), ["http://127.0.0.1:27125/v1/service/stop", "Bearer secret", { stop_key: "key" }]);
});

test("a service started outside Obsidian has no managed.json, so it isn't stopped", async () => {
  const { dir } = home();
  const launcher = new Launcher({ vaultPath: "/v", port: 1, home: dir, connect: async () => {},
    fetch: async () => assert.fail("asked to stop") });
  await assert.rejects(launcher.stopOther(), (error) => error.kind === "not-managed");
});

test("turned off while pairing, it starts nothing", async () => {
  const { dir } = home({ token: false });
  let paired;
  const launcher = new Launcher({ vaultPath: "/v", port: 1, home: dir, pollMs: 1, spawn: () => assert.fail("spawned"),
    execFile: (command, args, options, done) => { paired = done; }, connect: async () => assert.fail("connected") });
  const starting = launcher.start();
  await new Promise((resolve) => setImmediate(resolve));
  launcher.release();
  paired(null);
  await assert.rejects(starting, (error) => error.kind === "released");
});

test("turned off while waiting for it to answer, the service it spawned is told to stop", async () => {
  const { dir } = home();
  const child = fakeChild();
  let launcher;
  launcher = new Launcher({ vaultPath: "/v", port: 1, home: dir, pollMs: 1, spawn: () => child,
    connect: async () => { launcher.release(); throw new ConnectionProblem("no-service"); } });
  await assert.rejects(launcher.start(), (error) => error.kind === "released");
  assert.ok(child.stdin.ended, "its stdin closed, so it stops");
  assert.ok(!launcher.running());
});

test("an install.json from before PATH was recorded still finds uv's folder", async () => {
  const { dir, uv } = home({ searchPath: "" });
  let env;
  const launcher = new Launcher({ vaultPath: "/v", port: 1, home: dir, pollMs: 1,
    spawn: (command, args, options) => { env = options.env; return fakeChild(); }, connect: async () => ({}) });
  await launcher.start();
  assert.equal(env.PATH.split(path.delimiter)[0], path.dirname(uv));
});
