"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
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

test("restarting stops the service it started, waits for it to exit, and starts a new one", async () => {
  const { dir } = home();
  const managed = path.join(dir, ".nuru", "service", "managed.json");
  const children = [];
  let up = false;
  const launcher = new Launcher({ vaultPath: "/v", port: 27125, home: dir, pollMs: 1,
    spawn: () => {
      const child = fakeChild();
      children.push(child);
      fs.writeFileSync(managed, JSON.stringify({ pid: 1, port: 27125, stop_key: "key" }));
      up = true;
      return child;
    },
    connect: async () => {
      if (!up) throw new ConnectionProblem("no-service");
      return { status: "status" };
    },
    fetch: async (url) => {
      if (url.endsWith("/v1/service/stop")) {
        up = false;
        fs.rmSync(managed);
        setTimeout(() => { children.at(-1).exitCode = 0; children.at(-1).emit("exit", 0); }, 5); // exits a little later
        return { status: 202 };
      }
      if (!up) throw new TypeError("fetch failed");
      return { status: 200 };
    } });
  await launcher.start();
  assert.deepEqual(await launcher.restart(), { status: "status" });
  assert.equal(children.length, 2, "a new service, not the one on its way out");
  assert.equal(children[0].exitCode, 0);
  assert.ok(launcher.running());
});

test("restarting gives up with an error when the stopped service never exits", async () => {
  const { dir } = home();
  const managed = path.join(dir, ".nuru", "service", "managed.json");
  let spawned = 0;
  let up = false;
  const launcher = new Launcher({ vaultPath: "/v", port: 27125, home: dir, pollMs: 1, stopSeconds: 0.05,
    spawn: () => {
      spawned += 1;
      fs.writeFileSync(managed, JSON.stringify({ pid: 1, port: 27125, stop_key: "key" }));
      up = true;
      return fakeChild();
    },
    connect: async () => {
      if (!up) throw new ConnectionProblem("no-service");
      return { status: "status" };
    },
    fetch: async (url) => {
      if (url.endsWith("/v1/service/stop")) {
        up = false; // stops answering, but the process never exits
        return { status: 202 };
      }
      if (!up) throw new TypeError("fetch failed");
      return { status: 200 };
    } });
  await launcher.start();
  await assert.rejects(launcher.restart(), (error) => error.kind === "not-stopped");
  assert.equal(spawned, 1, "nothing new is started beside it");
});

test("restarting with nothing running starts it; one started outside Obsidian is left alone", async () => {
  const { dir } = home();
  let spawned = 0;
  let up = false;
  const launcher = new Launcher({ vaultPath: "/v", port: 27125, home: dir, pollMs: 1,
    spawn: () => { spawned += 1; up = true; return fakeChild(); },
    connect: async () => {
      if (!up) throw new ConnectionProblem("no-service");
      return { status: "status" };
    },
    fetch: async () => assert.fail("asked to stop") });
  assert.deepEqual(await launcher.restart(), { status: "status" });
  assert.equal(spawned, 1);
  launcher.release();
  await assert.rejects(launcher.restart(), (error) => error.kind === "not-managed", "it answers, with no stop key");
  assert.equal(spawned, 1);
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

test("Nuru starts by itself only once this version has connected, and never while a start failed or is unfinished", async () => {
  const { dir } = home();
  let fail = false;
  const launcher = new Launcher({ vaultPath: "/v", port: 1, home: dir, pollMs: 1, spawn: () => fakeChild(),
    connect: async () => ({ client: "c", status: "s" }) });
  const realStart = launcher.start.bind(launcher);
  launcher.start = async () => { if (fail) throw new Error("uv failed"); return realStart(); };
  const starts = path.join(dir, ".nuru", "service", "starts");

  assert.equal(launcher.mayStartByItself("1.0.3"), false, "never started on this computer");
  assert.deepEqual(await launcher.startMarked("1.0.3"), { client: "c", status: "s" });
  assert.equal(launcher.mayStartByItself("1.0.3"), true);
  assert.equal(launcher.mayStartByItself("1.0.4"), false, "the first time after an update");
  assert.deepEqual(fs.readdirSync(starts), [], "a start that connected leaves no mark");

  fail = true;
  await assert.rejects(launcher.startMarked("1.0.3"), /uv failed/);
  assert.equal(launcher.mayStartByItself("1.0.3"), false, "a failed start holds later launches");
  assert.equal(fs.readdirSync(starts).length, 1);
  fail = false;
  await launcher.startMarked("1.0.3");
  assert.equal(launcher.mayStartByItself("1.0.3"), true, "until a start connects, which clears this launcher's own marks");

  fs.writeFileSync(path.join(dir, ".nuru", "service", "autostart.json"), "{broken");
  assert.equal(launcher.mayStartByItself("1.0.3"), false);
});

test("overlapping starts from two vault windows never clear each other's marks; an ended window's are cleared", async () => {
  const { dir } = home();
  const make = (connect) => new Launcher({ vaultPath: "/v", port: 1, home: dir, pollMs: 1, spawn: () => fakeChild(), connect });
  const a = make(async () => ({ client: "a", status: "s" }));
  let open;
  const blocked = new Promise((resolve) => { open = resolve; });
  const b = make(async () => { await blocked; return { client: "b", status: "s" }; });
  const bStart = b.startMarked("1.0.3"); // still under way
  await a.startMarked("1.0.3");
  assert.equal(a.mayStartByItself("1.0.3"), false, "B's start is unfinished, so neither window starts by itself");
  open();
  await bStart;
  assert.equal(a.mayStartByItself("1.0.3"), true);

  // A window force-quit mid-start: its process is gone, and its mark stays until a start connects.
  const ended = childProcess.spawnSync(process.execPath, ["-e", "0"]).pid;
  fs.writeFileSync(path.join(dir, ".nuru", "service", "starts", `${ended}-gone`), "");
  assert.equal(a.mayStartByItself("1.0.3"), false);
  await a.startMarked("1.0.3");
  assert.deepEqual(fs.readdirSync(path.join(dir, ".nuru", "service", "starts")), []);
  assert.equal(a.mayStartByItself("1.0.3"), true);
});

test("a connecting start clears every mark but another window's start still under way, across plugin reloads", async (t) => {
  const { dir } = home();
  const make = (connect) => new Launcher({ vaultPath: "/v", port: 1, home: dir, pollMs: 1, spawn: () => fakeChild(), connect });
  const starts = path.join(dir, ".nuru", "service", "starts");

  // The plugin reloaded in the same Obsidian: the old launcher's start failed, a new one's connects.
  const before = make(async () => ({ client: "a", status: "s" }));
  before.start = async () => { throw new Error("uv failed"); };
  await assert.rejects(before.startMarked("1.0.3"), /uv failed/);
  assert.equal(fs.readFileSync(path.join(starts, fs.readdirSync(starts)[0]), "utf8"), "failed");
  const after = make(async () => ({ client: "b", status: "s" }));
  fs.writeFileSync(path.join(starts, `${process.pid}-abandoned`), ""); // this Obsidian's, not running any more

  // Another Obsidian window, still running: its start under way stays; its failed one, or one too old, goes.
  const other = childProcess.spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });
  t.after(() => other.kill());
  await new Promise((resolve) => other.once("spawn", resolve));
  fs.writeFileSync(path.join(starts, `${other.pid}-pending`), "");
  fs.writeFileSync(path.join(starts, `${other.pid}-failed`), "failed");
  fs.writeFileSync(path.join(starts, `${other.pid}-old`), "");
  const old = new Date(Date.now() - 60 * 60 * 1000);
  fs.utimesSync(path.join(starts, `${other.pid}-old`), old, old);

  await after.startMarked("1.0.3");
  assert.deepEqual(fs.readdirSync(starts), [`${other.pid}-pending`]);
  assert.equal(after.mayStartByItself("1.0.3"), false, "held while the other window's start is under way");
  fs.rmSync(path.join(starts, `${other.pid}-pending`));
  assert.equal(after.mayStartByItself("1.0.3"), true);
});

test("a connecting start isn't upset by another window clearing its mark during the sweep", async (t) => {
  const { dir } = home();
  const launcher = new Launcher({ vaultPath: "/v", port: 1, home: dir, pollMs: 1, spawn: () => fakeChild(),
    connect: async () => ({ client: "c", status: "s" }) });
  const starts = path.join(dir, ".nuru", "service", "starts");
  const other = childProcess.spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });
  t.after(() => other.kill());
  await new Promise((resolve) => other.once("spawn", resolve));
  fs.mkdirSync(starts, { recursive: true });
  const [gone, pending] = [path.join(starts, `${other.pid}-gone`), path.join(starts, `${other.pid}-pending`)];
  fs.writeFileSync(gone, "");
  fs.writeFileSync(pending, "");
  // The other window's start connects and clears its mark after this sweep stats it, before it reads it.
  const read = fs.readFileSync;
  t.mock.method(fs, "readFileSync", (file, ...rest) => {
    if (file === gone) fs.rmSync(gone, { force: true });
    return read(file, ...rest);
  });

  assert.deepEqual(await launcher.startMarked("1.0.3"), { client: "c", status: "s" });
  assert.deepEqual(fs.readdirSync(starts), [`${other.pid}-pending`]);
  fs.rmSync(pending);
  assert.equal(launcher.mayStartByItself("1.0.3"), true, "its success was recorded");
});
