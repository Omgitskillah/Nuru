// "Set up Nuru": installs the Nuru release this plugin was built with, so a vault needs no terminal.
// What it downloads, and the sha256 each file must have, are compiled into the plugin by the release
// build (build.js), so it trusts only what BRAT installed. Each install or repair goes into a new
// generation folder, ~/.nuru/app/<version>/<n>/, with its own uv and calculator, and is checked there
// before ~/.nuru/install.json is switched to it: a failure leaves the Nuru in use as it was. The
// previous generation stays as the rollback, and older ones are removed only after a switch.
"use strict";

const childProcess = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const https = require("node:https");
const os = require("node:os");
const path = require("node:path");

const { readTarGz, readZip, writeEntries } = require("./unpack");

/* global __NURU_RELEASE__ */
const RELEASE = typeof __NURU_RELEASE__ === "undefined" ? null : __NURU_RELEASE__;
const HOSTS = ["github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"];
const MAX_BYTES = 128 * 1024 * 1024;
const RUN_MS = 20 * 60 * 1000; // the first `uv sync` downloads Python and Nuru's packages
const ENV_FILE = /^\.env\.[\w-]+$/; // .env.nuru and the like, never .env.nuru.example

class SetupProblem extends Error {}

// This computer as release/dependencies.json names it; Windows on ARM runs the x64 builds.
function thisSystem(platform = process.platform, arch = process.arch) {
  const system = `${platform}-${arch}`;
  return system === "win32-arm64" ? "win32-x64" : system;
}

// The file at `url`, if its sha256 is `sha256`. Redirects are followed only to `hosts`, over
// `protocols`, at most `redirects` times; nothing is sent with a request.
function download(url, { sha256, get = https.get, hosts = HOSTS, protocols = ["https:"], redirects = 5,
  maxBytes = MAX_BYTES, timeoutMs = 10 * 60 * 1000 } = {}) {
  return new Promise((resolve, reject) => {
    let request;
    const fail = (message) => reject(new SetupProblem(`Couldn't download ${path.basename(new URL(url).pathname)}: ${message}`));
    const deadline = setTimeout(() => { request.destroy(); fail("it took too long"); }, timeoutMs);
    const done = (fn) => (value) => { clearTimeout(deadline); fn(value); };
    const fetch = (current, left) => {
      const where = new URL(current);
      if (!protocols.includes(where.protocol) || !hosts.includes(where.hostname)) return done(fail)(`it was sent to ${where.origin}`);
      request = get(where, { timeout: 60 * 1000 }, (response) => {
        const { statusCode, headers } = response;
        if (statusCode >= 300 && statusCode < 400 && headers.location) {
          response.resume();
          return left > 0 ? fetch(new URL(headers.location, where).href, left - 1) : done(fail)("too many redirects");
        }
        if (statusCode !== 200) {
          response.resume();
          return done(fail)(`the server answered ${statusCode}`);
        }
        const chunks = [];
        let size = 0;
        response.on("data", (chunk) => {
          if ((size += chunk.length) > maxBytes) {
            request.destroy();
            done(fail)("it is larger than expected");
          } else {
            chunks.push(chunk);
          }
        });
        response.on("end", () => {
          if (size > maxBytes) return;
          const data = Buffer.concat(chunks);
          if (crypto.createHash("sha256").update(data).digest("hex") !== sha256) return done(fail)("it isn't the file this release names");
          done(resolve)(data);
        });
        response.on("error", (error) => done(fail)(error.message));
      });
      request.on("timeout", () => request.destroy(new Error("the connection stalled")));
      request.on("error", (error) => done(fail)(error.message));
    };
    fetch(url, redirects);
  });
}

// Runs a program without a shell, so a path with spaces is one argument. Resolves {code, output}.
function run(command, args, { cwd, env, timeoutMs = RUN_MS } = {}) {
  return new Promise((resolve) => {
    let output = "";
    const child = childProcess.spawn(command, args, { cwd, env, shell: false, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    const timer = setTimeout(() => child.kill(), timeoutMs);
    for (const stream of [child.stdout, child.stderr]) stream.on("data", (chunk) => { output = (output + chunk).slice(-4000); });
    child.on("error", (error) => { output += error.message; });
    child.on("close", (code) => { clearTimeout(timer); resolve({ code, output }); });
  });
}

function lastLines(text, n = 5) {
  return text.trimEnd().split(/\r?\n/).slice(-n).join("\n");
}

// One setup at a time, across Obsidian windows. Each setup adds its own entry to ~/.nuru/setup-locks/,
// named after its process, and goes on only if no other live entry is there; otherwise it removes its
// own and stops. Of two setups starting together, at least the second to look sees the first, so two
// never both go on. An entry is only ever removed by its own setup, or once its process has ended or it
// is older than any setup runs (Obsidian quit mid-setup): never another live setup's.
const LOCK_MS = 2 * 60 * 60 * 1000; // downloads stop at 10 minutes each, uv at 20

function lock(dir) {
  const locks = path.join(dir, "setup-locks");
  fs.mkdirSync(locks, { recursive: true });
  const mine = path.join(locks, `${process.pid}-${crypto.randomUUID()}`);
  fs.writeFileSync(mine, "", { flag: "wx" });
  const others = fs.readdirSync(locks).map((name) => path.join(locks, name)).filter((entry) => entry !== mine && !ended(entry));
  if (others.length) {
    fs.rmSync(mine, { force: true });
    throw new SetupProblem("Nuru is already being set up, in another Obsidian window.");
  }
  return () => fs.rmSync(mine, { force: true });
}

// Whether a lock entry's setup has ended, removing the entry if so.
function ended(entry) {
  const pid = Number(path.basename(entry).split("-")[0]);
  const stat = fs.statSync(entry, { throwIfNoEntry: false });
  if (stat && Date.now() - stat.mtimeMs < LOCK_MS && pid > 0 && alive(pid)) return false;
  fs.rmSync(entry, { force: true });
  return true;
}

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM"; // there, but someone else's
  }
}

function previousInstall(file) {
  try {
    const { nuru } = JSON.parse(fs.readFileSync(file, "utf8"));
    return typeof nuru === "string" && path.isAbsolute(nuru) && fs.statSync(nuru).isDirectory() ? nuru : null;
  } catch {
    return null;
  }
}

// A new, empty generation folder, numbered after the highest there (setups run one at a time).
function newGeneration(versionDir) {
  fs.mkdirSync(versionDir, { recursive: true });
  const n = Math.max(0, ...fs.readdirSync(versionDir).filter((name) => /^\d+$/.test(name)).map(Number));
  const dir = path.join(versionDir, String(n + 1));
  fs.mkdirSync(dir);
  return dir;
}

// install.json, replaced in one rename after its contents (`text`) are on disk.
function switchInstall(file, text) {
  const temporary = `${file}.tmp`;
  const fd = fs.openSync(temporary, "w", 0o600);
  try {
    fs.writeSync(fd, text);
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  fs.renameSync(temporary, file);
  if (process.platform !== "win32") {
    const dir = fs.openSync(path.dirname(file), "r");
    try { fs.fsyncSync(dir); } finally { fs.closeSync(dir); }
  }
}

// Every generation but `keep`, and version folders left empty. What can't be removed now (a file still
// in use on Windows) stays until the next setup: the switch has already happened.
function prune(appDir, keep) {
  for (const version of fs.readdirSync(appDir)) {
    const versionDir = path.join(appDir, version);
    try {
      for (const generation of fs.readdirSync(versionDir)) {
        const dir = path.join(versionDir, generation);
        if (!keep.includes(dir)) fs.rmSync(dir, { recursive: true, force: true, maxRetries: 2 });
      }
      if (!fs.readdirSync(versionDir).length) fs.rmdirSync(versionDir);
    } catch {
      // left for the next setup
    }
  }
}

async function setUp({ home = os.homedir(), release = RELEASE, system = thisSystem(), fetch = download, runner = run,
  step = () => {} } = {}) {
  if (!release) {
    throw new SetupProblem("This copy of the plugin isn't from a Nuru release, so it can't set Nuru up. " +
      "Install the plugin with BRAT from the Nuru repository, or run `node plugins/obsidian/install.js <vault>` in a Nuru checkout.");
  }
  const uvPin = release.uv.systems[system];
  const mathPin = release["math-mcp"].systems[system];
  if (!uvPin || !mathPin) throw new SetupProblem(`Nuru has no release for this computer (${system}): it runs on Windows and macOS.`);
  const nuruDir = path.join(home, ".nuru");
  fs.mkdirSync(nuruDir, { recursive: true, mode: 0o700 });
  const unlock = lock(nuruDir);
  let generation;
  try {
    step("Downloading Nuru…");
    const bundle = await fetch(release.url + release.bundle.asset, { sha256: release.bundle.sha256 });
    step("Downloading uv…");
    const uvArchive = await fetch(release.uv.url + uvPin.asset, { sha256: uvPin.sha256 });
    step("Downloading the calculator…");
    const calculator = await fetch(release["math-mcp"].url + mathPin.asset, { sha256: mathPin.sha256 });

    step("Unpacking…");
    const appDir = path.join(nuruDir, "app");
    generation = newGeneration(path.join(appDir, release.version));
    writeEntries(readZip(bundle, `nuru-${release.version}/`), generation);
    const uvDir = path.join(generation, "uv");
    const uvEntries = uvPin.asset.endsWith(".zip") ? readZip(uvArchive, "") : readTarGz(uvArchive, `${uvPin.asset.replace(/\.tar\.gz$/, "")}/`);
    writeEntries(uvEntries, uvDir);
    const exe = system.startsWith("win32") ? ".exe" : "";
    const calculatorFile = path.join(generation, "servers", "math-4-llms", "bin", `math-mcp${exe}`);
    writeEntries([{ parts: [path.basename(calculatorFile)], executable: true, data: calculator }], path.dirname(calculatorFile));
    const installFile = path.join(nuruDir, "install.json");
    const previous = previousInstall(installFile);
    const previousText = previous && fs.readFileSync(installFile, "utf8");
    for (const name of previous ? fs.readdirSync(previous).filter((n) => ENV_FILE.test(n)) : []) {
      fs.copyFileSync(path.join(previous, name), path.join(generation, name), fs.constants.COPYFILE_EXCL);
    }

    const uv = path.join(uvDir, `uv${exe}`);
    const env = { ...process.env, PATH: [uvDir, process.env.PATH].filter(Boolean).join(path.delimiter) };
    delete env.VIRTUAL_ENV;
    step("Installing Python and Nuru's packages (the first time takes a few minutes)…");
    const sync = await runner(uv, ["sync", "--locked"], { cwd: generation, env });
    if (sync.code !== 0) throw new SetupProblem(`Installing Nuru's packages failed:\n${lastLines(sync.output)}`);
    step("Checking the new install…");
    const check = await runner(uv, ["run", "--locked", "nuru-serve", "--help"], { cwd: generation, env });
    if (check.code !== 0) throw new SetupProblem(`The new install doesn't start:\n${lastLines(check.output)}`);
    const onDisk = fs.readFileSync(calculatorFile);
    if (crypto.createHash("sha256").update(onDisk).digest("hex") !== mathPin.sha256) {
      throw new SetupProblem("The calculator changed after it was saved: an antivirus program may have removed or altered it.");
    }

    switchInstall(installFile, `${JSON.stringify({ nuru: generation, uv, version: release.version }, null, 2)}\n`);
    const installed = generation;
    generation = null;
    prune(appDir, [installed, ...(previous ? [previous] : [])]);
    return { version: release.version, nuru: installed, previous: previousText };
  } finally {
    if (generation) fs.rmSync(generation, { recursive: true, force: true, maxRetries: 2 });
    unlock();
  }
}

// Puts back the install.json a setup replaced (setUp's `previous`), for when the new install doesn't
// start: the previous generation is still there, kept as the rollback.
function restoreInstall(home, previous) {
  switchInstall(path.join(home, ".nuru", "install.json"), previous);
}

module.exports = { HOSTS, RELEASE, SetupProblem, download, lock, restoreInstall, run, setUp, thisSystem };
