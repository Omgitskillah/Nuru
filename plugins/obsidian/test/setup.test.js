"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const { SetupProblem, download, fileSha256, run, setUp, thisSystem } = require("../lib/setup");
const { tarGz, zip } = require("./helpers/archives");
const { until } = require("./helpers/setup");

const POSIX = process.platform !== "win32";
const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");

// A web server on 127.0.0.1: /assets/<name> serves `files`, and /redirect/<n>/<name> redirects n times
// before getting there. `stall` never answers.
async function server(t, files) {
  const asked = [];
  const srv = http.createServer((request, response) => {
    asked.push(request.url);
    const redirect = request.url.match(/^\/redirect\/(\d+)\/(.+)$/);
    if (redirect) {
      const left = Number(redirect[1]);
      response.writeHead(302, { location: left > 1 ? `/redirect/${left - 1}/${redirect[2]}` : `/assets/${redirect[2]}` });
      return response.end();
    }
    if (request.url === "/stall") return undefined;
    const name = request.url.replace(/^\/assets\//, "");
    if (!(name in files)) {
      response.writeHead(404);
      return response.end();
    }
    response.writeHead(200);
    return response.end(files[name]);
  });
  await new Promise((resolve) => srv.listen(0, "127.0.0.1", resolve));
  t.after(() => { srv.closeAllConnections(); srv.close(); });
  return { url: `http://127.0.0.1:${srv.address().port}`, asked };
}

const local = (options) => ({ get: http.get, hosts: ["127.0.0.1"], protocols: ["http:"], ...options });

// A release like the one build.js compiles in, served locally, for this computer's system.
async function release(t, { version = "0.1.0", system = thisSystem() } = {}) {
  const windows = system.startsWith("win32");
  const files = {
    [`nuru-${version}.zip`]: zip([
      { name: `nuru-${version}/` },
      { name: `nuru-${version}/pyproject.toml`, data: "[tool.uv.workspace]\n" },
      { name: `nuru-${version}/.env.nuru.example`, data: "# NURU_BASE_MODEL=\n" },
      { name: `nuru-${version}/servers/math-4-llms/Cargo.toml`, data: "[package]\n" },
    ]),
    "uv-archive": windows ? zip([{ name: "uv.exe", data: "uv for windows" }])
      : tarGz([{ name: "uv-test/", type: "5" }, { name: "uv-test/uv", data: "uv for mac", mode: 0o755 }]),
    "math-mcp-asset": "calculator",
  };
  const { url, asked } = await server(t, files);
  const uvAsset = windows ? "uv-archive.zip" : "uv-test.tar.gz";
  files[uvAsset] = files["uv-archive"];
  const facts = {
    version, url: `${url}/redirect/2/`,
    bundle: { asset: `nuru-${version}.zip`, sha256: sha256(files[`nuru-${version}.zip`]) },
    uv: { version: "0.12.13", url: `${url}/assets/`, systems: { [system]: { asset: uvAsset, sha256: sha256(files["uv-archive"]) } } },
    "math-mcp": { version: "v1.0.1", url: `${url}/assets/`, systems: { [system]: { asset: "math-mcp-asset", sha256: sha256("calculator") } } },
  };
  return { facts, asked, files };
}

function home(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "nuru setup home "));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

// Stands in for uv: records each call and answers as `answer` says (default: success).
function fakeUv(answer = () => ({ code: 0, output: "" })) {
  const calls = [];
  return { calls, runner: async (command, args, options) => { calls.push({ command, args, ...options }); return answer(args, options); } };
}

const fetchLocal = (url, options) => download(url, local(options));
const installJson = (dir) => JSON.parse(fs.readFileSync(path.join(dir, ".nuru", "install.json"), "utf8"));

for (const system of ["darwin-arm64", "win32-x64"]) {
  test(`Set up Nuru downloads, checks and installs a first generation, then switches install.json to it (${system})`, async (t) => {
    await firstSetup(t, system);
  });
}

async function firstSetup(t, system) {
  const dir = home(t);
  const { facts, asked } = await release(t, { system });
  const uv = fakeUv();
  const steps = [];

  const result = await setUp({ home: dir, release: facts, system, fetch: fetchLocal, runner: uv.runner, step: (s) => steps.push(s) });

  const generation = path.join(dir, ".nuru", "app", "0.1.0", "1");
  const exe = system.startsWith("win32") ? ".exe" : "";
  assert.deepEqual(result, { version: "0.1.0", nuru: generation, previous: null });
  assert.deepEqual(installJson(dir), { nuru: generation, uv: path.join(generation, "uv", `uv${exe}`), version: "0.1.0" });
  assert.equal(fs.readFileSync(path.join(generation, "pyproject.toml"), "utf8"), "[tool.uv.workspace]\n");
  const calculator = path.join(generation, "servers", "math-4-llms", "bin", `math-mcp${exe}`);
  assert.equal(fs.readFileSync(calculator, "utf8"), "calculator");
  if (POSIX) {
    assert.equal(fs.statSync(calculator).mode & 0o777, 0o755, "the calculator can run on macOS");
    if (!exe) assert.equal(fs.statSync(path.join(generation, "uv", "uv")).mode & 0o777, 0o755);
    assert.equal(fs.statSync(path.join(dir, ".nuru", "install.json")).mode & 0o777, 0o600);
  }
  assert.deepEqual(uv.calls.map((c) => [c.command, c.args, c.cwd]), [
    [path.join(generation, "uv", `uv${exe}`), ["sync", "--locked"], generation],
    [path.join(generation, "uv", `uv${exe}`), ["run", "--locked", "nuru-serve", "--help"], generation]]);
  assert.equal(uv.calls[0].env.PATH.split(path.delimiter)[0], path.join(generation, "uv"));
  assert.equal(uv.calls[0].env.VIRTUAL_ENV, undefined);
  assert.ok(asked.includes("/redirect/2/nuru-0.1.0.zip") && asked.includes("/assets/nuru-0.1.0.zip"), "the bundle came through two redirects");
  assert.deepEqual(steps, ["Downloading Nuru…", "Downloading uv…", "Downloading the calculator…", "Unpacking…",
    "Installing Python and Nuru's packages (the first time takes a few minutes)…", "Checking the new install…"]);
  assert.deepEqual(fs.readdirSync(path.join(dir, ".nuru")).sort(), ["app", "install.json", "setup-locks"]);
  assert.deepEqual(fs.readdirSync(path.join(dir, ".nuru", "setup-locks")), [], "no lock or temporary file is left");
}

test("an install.json naming nothing usable, from an old or broken install, is replaced", async (t) => {
  for (const recorded of ["not json", JSON.stringify({ nuru: "relative/path" }), JSON.stringify({ nuru: 7 }),
    JSON.stringify({ nuru: path.join(os.tmpdir(), "nuru-no-such-folder") })]) {
    const dir = home(t);
    fs.mkdirSync(path.join(dir, ".nuru", "app"), { recursive: true });
    fs.writeFileSync(path.join(dir, ".nuru", "install.json"), recorded);
    fs.writeFileSync(path.join(dir, ".nuru", "app", "stray.txt"), "left alone");
    const { facts } = await release(t);

    const { nuru } = await setUp({ home: dir, release: facts, fetch: fetchLocal, runner: fakeUv().runner });

    assert.equal(installJson(dir).nuru, nuru);
    assert.equal(fs.readFileSync(path.join(dir, ".nuru", "app", "stray.txt"), "utf8"), "left alone");
  }
});

test("a release build carries the release's facts in the plugin", () => {
  const file = require.resolve("../lib/setup");
  const facts = { version: "9.9.9" };
  globalThis.__NURU_RELEASE__ = facts;
  delete require.cache[file];
  try {
    assert.equal(require("../lib/setup").RELEASE, facts);
  } finally {
    delete globalThis.__NURU_RELEASE__;
    delete require.cache[file];
  }
  assert.equal(require("../lib/setup").RELEASE, null, "any other build has none");
});

test("each repair is a new generation that keeps the settings, and the one before stays as the rollback", async (t) => {
  const dir = home(t);
  const { facts } = await release(t);
  const options = { home: dir, release: facts, fetch: fetchLocal, runner: fakeUv().runner };
  const first = (await setUp(options)).nuru;
  fs.writeFileSync(path.join(first, ".env.nuru"), "NURU_BASE_MODEL=qwen3.5:4b\n");
  fs.writeFileSync(path.join(first, ".env.deep-think"), "CLAUDE_API_KEY=sk-ant-test\n", { mode: 0o600 });
  fs.writeFileSync(path.join(first, ".env.nuru.bak-20261005"), "old\n");

  const second = (await setUp(options)).nuru;
  const third = (await setUp(options)).nuru;

  assert.deepEqual([first, second, third].map((g) => path.basename(g)), ["1", "2", "3"]);
  assert.equal(installJson(dir).nuru, third);
  assert.equal(fs.readFileSync(path.join(third, ".env.nuru"), "utf8"), "NURU_BASE_MODEL=qwen3.5:4b\n");
  assert.equal(fs.readFileSync(path.join(third, ".env.deep-think"), "utf8"), "CLAUDE_API_KEY=sk-ant-test\n");
  if (POSIX) assert.equal(fs.statSync(path.join(third, ".env.deep-think")).mode & 0o777, 0o600, "the key stays private");
  assert.ok(!fs.existsSync(path.join(third, ".env.nuru.bak-20261005")), "only .env.<name> files are settings");
  assert.equal(fs.readFileSync(path.join(third, ".env.nuru.example"), "utf8"), "# NURU_BASE_MODEL=\n", "the release's own example");
  assert.deepEqual(fs.readdirSync(path.join(dir, ".nuru", "app", "0.1.0")).sort(), ["2", "3"]);
});

test("updating to a new version keeps the old version's generation as the rollback, and its settings", async (t) => {
  const dir = home(t);
  const old = await setUp({ home: dir, release: (await release(t, { version: "0.1.0" })).facts, fetch: fetchLocal, runner: fakeUv().runner });
  fs.writeFileSync(path.join(old.nuru, ".env.obsidian"), "VAULT_PATH=/notes\n");

  const updated = await setUp({ home: dir, release: (await release(t, { version: "0.2.0" })).facts, fetch: fetchLocal, runner: fakeUv().runner });

  assert.equal(updated.nuru, path.join(dir, ".nuru", "app", "0.2.0", "1"));
  assert.equal(fs.readFileSync(path.join(updated.nuru, ".env.obsidian"), "utf8"), "VAULT_PATH=/notes\n");
  assert.ok(fs.existsSync(old.nuru));

  await setUp({ home: dir, release: (await release(t, { version: "0.3.0" })).facts, fetch: fetchLocal, runner: fakeUv().runner });
  assert.deepEqual(fs.readdirSync(path.join(dir, ".nuru", "app")).sort(), ["0.2.0", "0.3.0"], "an emptied version folder goes too");
});

test("a failure anywhere before the switch leaves the Nuru in use as it was", async (t) => {
  const failures = {
    "packages don't install": [(args) => ({ code: args[0] === "sync" ? 1 : 0, output: "resolving\nerror: no network\n" }),
      /Installing Nuru's packages failed:\nresolving\nerror: no network/],
    "the new install doesn't start": [(args) => ({ code: args[0] === "run" ? 2 : 0, output: "Traceback\nImportError: x" }),
      /The new install doesn't start:\nTraceback\nImportError: x/],
    "an antivirus program alters the calculator": [(args, { cwd }) => {
      if (args[0] === "run") fs.writeFileSync(path.join(cwd, "servers", "math-4-llms", "bin", `math-mcp${POSIX ? "" : ".exe"}`), "quarantined");
      return { code: 0, output: "" };
    }, /The calculator changed after it was saved/],
  };
  for (const [what, [answer, message]] of Object.entries(failures)) {
    await t.test(what, async (t) => {
      const dir = home(t);
      const { facts } = await release(t);
      const good = await setUp({ home: dir, release: facts, fetch: fetchLocal, runner: fakeUv().runner });
      const before = fs.readFileSync(path.join(dir, ".nuru", "install.json"), "utf8");

      await assert.rejects(setUp({ home: dir, release: facts, fetch: fetchLocal, runner: fakeUv(answer).runner }),
        (error) => error instanceof SetupProblem && message.test(error.message));

      assert.equal(fs.readFileSync(path.join(dir, ".nuru", "install.json"), "utf8"), before);
      assert.deepEqual(fs.readdirSync(path.join(dir, ".nuru", "app", "0.1.0")), ["1"], "the failed generation is gone");
      assert.ok(fs.existsSync(good.nuru));
      assert.deepEqual(fs.readdirSync(path.join(dir, ".nuru", "setup-locks")), []);
    });
  }
});

test("a download that isn't the file the release names stops the setup before anything is unpacked", async (t) => {
  const dir = home(t);
  const { facts } = await release(t);
  facts.uv.systems[thisSystem()].sha256 = "0".repeat(64);

  await assert.rejects(setUp({ home: dir, release: facts, fetch: fetchLocal, runner: fakeUv().runner }),
    /Couldn't download uv-.*: it isn't the file this release names/);
  assert.deepEqual(fs.readdirSync(path.join(dir, ".nuru")), ["setup-locks"]);
  assert.deepEqual(fs.readdirSync(path.join(dir, ".nuru", "setup-locks")), []);
});

test("downloads only follow redirects to allowed hosts and protocols, and only so far", async (t) => {
  const { url } = await server(t, { file: "content" });
  const ok = sha256("content");
  assert.equal(String(await download(`${url}/redirect/3/file`, local({ sha256: ok }))), "content");
  await assert.rejects(download(`${url}/redirect/3/file`, local({ sha256: ok, redirects: 2 })), /Couldn't download file: too many redirects/);
  await assert.rejects(download(`${url}/assets/file`, local({ sha256: ok, hosts: ["github.com"] })), /it was sent to http:\/\/127\.0\.0\.1:\d+/);
  await assert.rejects(download(`${url}/assets/file`, { sha256: ok, get: http.get, hosts: ["127.0.0.1"] }), /it was sent to http:/, "HTTPS only by default");
  await assert.rejects(download(`${url}/assets/missing`, local({ sha256: ok })), /Couldn't download missing: the server answered 404/);
  await assert.rejects(download(`${url}/assets/file`, local({ sha256: ok, maxBytes: 3 })), /it is larger than expected/);
  await assert.rejects(download(`${url}/stall`, local({ sha256: ok, timeoutMs: 200 })), /Couldn't download stall: it took too long/);
  await assert.rejects(download("http://127.0.0.1:1/x", local({ sha256: ok })), /Couldn't download x: .*ECONNREFUSED/);
});

test("a large download is streamed to a file, kept only once its sha256 matches", async (t) => {
  const dir = home(t);
  const { url } = await server(t, { big: "x".repeat(100000) });
  const to = path.join(dir, "big.exe");

  assert.equal(await download(`${url}/redirect/1/big`, local({ sha256: sha256("x".repeat(100000)), to })), to);
  assert.equal(fs.readFileSync(to, "utf8"), "x".repeat(100000));
  assert.deepEqual(fs.readdirSync(dir), ["big.exe"], "no .partial left");

  fs.rmSync(to);
  await assert.rejects(download(`${url}/assets/big`, local({ sha256: "0".repeat(64), to })), /it isn't the file this release names/);
  await assert.rejects(download(`${url}/assets/big`, local({ sha256: sha256("x".repeat(100000)), to, maxBytes: 10 })), /larger than expected/);
  await assert.rejects(download(`${url}/assets/missing`, local({ sha256: "", to })), /answered 404/);
  assert.deepEqual(fs.readdirSync(dir), [], "a failed download leaves nothing");
});

test("two downloads to one file each write their own partial, and both end with the checked file", async (t) => {
  const dir = home(t);
  const body = "y".repeat(300000);
  const { url } = await server(t, { big: body });
  const to = path.join(dir, "OllamaSetup.exe");
  const both = await Promise.all([1, 2].map(() => download(`${url}/assets/big`, local({ sha256: sha256(body), to }))));
  assert.deepEqual(both, [to, to]);
  assert.equal(fs.readFileSync(to, "utf8"), body);
  assert.deepEqual(fs.readdirSync(dir), ["OllamaSetup.exe"]);
});

test("a file that can't be created, written or moved ends the download as a failure, leaving nothing", async (t) => {
  const dir = home(t);
  const { url } = await server(t, { big: "z".repeat(1000) });
  const ok = sha256("z".repeat(1000));
  await assert.rejects(download(`${url}/assets/big`, local({ sha256: ok, to: path.join(dir, "missing", "big") })), /Couldn't download big: ENOENT/);

  const write = fs.writeSync;
  fs.writeSync = () => { throw Object.assign(new Error("ENOSPC: no space left on device"), { code: "ENOSPC" }); };
  try {
    await assert.rejects(download(`${url}/assets/big`, local({ sha256: ok, to: path.join(dir, "big") })), /no space left on device/);
  } finally {
    fs.writeSync = write;
  }
  assert.deepEqual(fs.readdirSync(dir), []);

  fs.mkdirSync(path.join(dir, "taken", "inside"), { recursive: true }); // a folder where the file goes
  await assert.rejects(download(`${url}/assets/big`, local({ sha256: ok, to: path.join(dir, "taken") })), /Couldn't download big:/);
  assert.deepEqual(fs.readdirSync(dir), ["taken"]);
  assert.equal(await fileSha256(path.join(__filename)), sha256(fs.readFileSync(__filename)));
});

test("a stalled connection and a broken response are reported", async (t) => {
  const stalls = (url, options, onResponse) => {
    const request = http.get(url, { ...options, timeout: 100 }, onResponse);
    return request;
  };
  const { url } = await server(t, {});
  await assert.rejects(download(`${url}/stall`, local({ sha256: "", get: stalls })), /the connection stalled/);

  const breaks = http.createServer((request, response) => {
    response.writeHead(200, { "content-length": 100 });
    response.write("part");
    setTimeout(() => response.destroy(), 20);
  });
  await new Promise((resolve) => breaks.listen(0, "127.0.0.1", resolve));
  t.after(() => breaks.close());
  await assert.rejects(download(`http://127.0.0.1:${breaks.address().port}/x`, local({ sha256: "" })), /Couldn't download x: (aborted|socket hang up)/);
});

test("a setup goes on only when no other live setup holds the lock, and clears the entries of ended ones", async (t) => {
  const dir = home(t);
  const { facts } = await release(t);
  const locks = path.join(dir, ".nuru", "setup-locks");
  fs.mkdirSync(locks, { recursive: true });
  const other = path.join(locks, `${process.pid}-another-window`);
  fs.writeFileSync(other, "");
  await assert.rejects(setUp({ home: dir, release: facts, fetch: fetchLocal, runner: fakeUv().runner }),
    /Nuru is already being set up, in another Obsidian window/);
  assert.deepEqual(fs.readdirSync(locks), [path.basename(other)], "the other setup's entry stays, this one's goes");
  fs.rmSync(other); // that setup ends
  if (POSIX) {
    fs.writeFileSync(path.join(locks, "1-launchd"), ""); // running, but not ours to signal
    await assert.rejects(setUp({ home: dir, release: facts, fetch: fetchLocal, runner: fakeUv().runner }), /already being set up/);
    fs.rmSync(path.join(locks, "1-launchd"));
  }

  const gone = childProcess.spawnSync(process.execPath, ["-e", "0"]).pid;
  const old = path.join(locks, `${process.pid}-left-hours-ago`);
  for (const entry of [path.join(locks, `${gone}-quit-mid-setup`), path.join(locks, "not-a-pid"), old]) fs.writeFileSync(entry, "");
  const hoursAgo = new Date(Date.now() - 3 * 60 * 60 * 1000);
  fs.utimesSync(old, hoursAgo, hoursAgo);
  assert.equal((await setUp({ home: dir, release: facts, fetch: fetchLocal, runner: fakeUv().runner })).version, "0.1.0");
  assert.deepEqual(fs.readdirSync(locks), []);
});

test("two processes taking the lock together never both hold it", async (t) => {
  const dir = home(t);
  const log = path.join(dir, "log");
  const script = `
    const fs = require("node:fs");
    const { lock } = require(${JSON.stringify(require.resolve("../lib/setup"))});
    let held = 0;
    for (let i = 0; i < 200; i++) {
      let unlock;
      try { unlock = lock(${JSON.stringify(dir)}); } catch { continue; }
      fs.appendFileSync(${JSON.stringify(log)}, "in\\n");
      const until = Date.now() + 2; while (Date.now() < until);
      fs.appendFileSync(${JSON.stringify(log)}, "out\\n");
      unlock();
      held++;
    }
    console.log(held);`;
  const run2 = () => new Promise((resolve) => {
    const child = childProcess.spawn(process.execPath, ["-e", script], { stdio: ["ignore", "pipe", "inherit"] });
    let out = "";
    child.stdout.on("data", (chunk) => { out += chunk; });
    child.on("close", () => resolve(Number(out)));
  });

  const held = await Promise.all([run2(), run2()]);

  const lines = fs.readFileSync(log, "utf8").trim().split("\n");
  assert.equal(lines.length, 2 * (held[0] + held[1]));
  lines.forEach((line, i) => assert.equal(line, i % 2 ? "out" : "in", `line ${i}: one holder at a time`));
  assert.ok(held[0] + held[1] > 0);
  assert.deepEqual(fs.readdirSync(path.join(dir, "setup-locks")), []);
});

test("a running Nuru restarts on the new install, and goes back to the previous one if it doesn't start", async (t) => {
  for (const [failures, expect] of [[0, {}], [1, { rolledBack: true }], [2, { rollbackError: "exited" }]]) {
    const dir = home(t);
    const { facts } = await release(t);
    const options = { home: dir, release: facts, fetch: fetchLocal, runner: fakeUv().runner };
    const first = await setUp(options);
    const before = fs.readFileSync(path.join(dir, ".nuru", "install.json"), "utf8");
    const started = [];
    let left = failures;
    const start = async () => {
      started.push(installJson(dir).nuru);
      assert.equal(fs.readdirSync(path.join(dir, ".nuru", "setup-locks")).length, 1, "the setup lock is held");
      if (left-- > 0) throw new Error("exited");
    };

    const result = await setUp({ ...options, start });

    assert.equal(result.previous, before);
    assert.equal(result.startError?.message, failures ? "exited" : undefined);
    assert.equal(result.rolledBack, expect.rolledBack);
    assert.equal(result.rollbackError?.message, expect.rollbackError);
    assert.deepEqual(started, failures ? [result.nuru, first.nuru] : [result.nuru]);
    assert.equal(installJson(dir).nuru, failures ? first.nuru : result.nuru);
    assert.ok(fs.existsSync(first.nuru) && fs.existsSync(result.nuru));
  }
});

test("a first install that doesn't start has nothing to go back to, and stays selected", async (t) => {
  const dir = home(t);
  const { facts } = await release(t);
  const result = await setUp({ home: dir, release: facts, fetch: fetchLocal, runner: fakeUv().runner,
    start: async () => { throw new Error("exited"); } });
  assert.equal(result.previous, null);
  assert.equal(result.startError.message, "exited");
  assert.equal(result.rolledBack, undefined);
  assert.equal(installJson(dir).nuru, result.nuru);
});

test("another window's setup can't run while one restarts or rolls back, so a late rollback never undoes it", async (t) => {
  const dir = home(t);
  const { facts } = await release(t);
  const options = { home: dir, release: facts, fetch: fetchLocal, runner: fakeUv().runner };
  const first = await setUp(options);
  let fail;
  const failed = new Promise((resolve) => { fail = resolve; });
  let calls = 0;
  const windowA = setUp({ ...options, start: async () => { if (calls++ === 0) { await failed; throw new Error("exited"); } } });
  await until(() => calls === 1); // A has switched install.json and is restarting

  await assert.rejects(setUp(options), /already being set up, in another Obsidian window/, "window B waits its turn");
  fail();
  const a = await windowA;
  assert.equal(a.rolledBack, true);
  assert.equal(installJson(dir).nuru, first.nuru);

  const b = await setUp(options); // B, after A has finished
  assert.equal(installJson(dir).nuru, b.nuru, "B's selection stands");
});

test("a plugin not built for a release, or a computer with no release, says so and changes nothing", async (t) => {
  const dir = home(t);
  await assert.rejects(setUp({ home: dir, release: null }), /isn't from a Nuru release.*install the plugin with BRAT/is);
  const { facts } = await release(t, { system: "linux-x64" });
  await assert.rejects(setUp({ home: dir, release: facts, system: "freebsd-x64" }),
    /Nuru has no release for this computer \(freebsd-x64\): it runs on Windows and macOS/);
  assert.deepEqual(fs.readdirSync(dir), []);
});

test("this computer is named as release/dependencies.json names it, which pins uv, the calculator and Ollama for each", () => {
  assert.equal(thisSystem("darwin", "arm64"), "darwin-arm64");
  assert.equal(thisSystem("win32", "x64"), "win32-x64");
  assert.equal(thisSystem("win32", "arm64"), "win32-x64", "Windows on ARM runs the x64 builds");
  const pins = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "..", "release", "dependencies.json"), "utf8"));
  for (const name of ["uv", "math-mcp", "ollama"]) {
    assert.deepEqual(Object.keys(pins[name].systems).sort(), ["darwin-arm64", "darwin-x64", "win32-x64"]);
    assert.match(pins[name].url, new RegExp(`^https://github\\.com/.*/v?${pins[name].version.replace(/\./g, "\\.")}/$`));
    for (const { sha256: digest } of Object.values(pins[name].systems)) assert.match(digest, /^[0-9a-f]{64}$/);
  }
  assert.ok(Object.values(pins.uv.systems).every(({ asset }) => /\.(zip|tar\.gz)$/.test(asset)));
});

test("run starts a program without a shell, in a folder with spaces, and gives its exit code and output", async (t) => {
  const dir = home(t);
  const result = await run(process.execPath, ["-e", "console.log(process.cwd()); console.error('to stderr'); process.exit(3)"], { cwd: dir });
  assert.equal(result.code, 3);
  assert.match(result.output, /to stderr/);
  assert.ok(result.output.includes(fs.realpathSync(dir)) || result.output.includes(dir));

  const slow = await run(process.execPath, ["-e", "setTimeout(() => {}, 60000)"], { timeoutMs: 100 });
  assert.notEqual(slow.code, 0);

  const missing = await run(path.join(dir, "no such program"), []);
  assert.notEqual(missing.code, 0);
  assert.match(missing.output, /ENOENT/);
});
