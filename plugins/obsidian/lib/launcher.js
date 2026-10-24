// Starting nuru-serve from Obsidian, so a vault needs no terminal: `uv run nuru-serve --managed` for this
// vault, from the Nuru folder that install.js recorded in ~/.nuru/install.json. Never from plugin data,
// which lives in the vault and syncs: a synced vault could otherwise name a folder of its own and have
// its code run when the vault opens. The service's output goes to ~/.nuru/service/serve.log.
//
// A service this plugin started stops when the plugin or Obsidian goes: its stdin is a pipe from here.
// One another vault's plugin started is switched to this vault with the stop key in
// ~/.nuru/service/managed.json, which the service refuses while it has a turn. A service started from
// a terminal or at login writes no key, so it's never stopped from here.
"use strict";

const childProcess = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { ConnectionProblem, tokenPath } = require("./connection");

const START_SECONDS = 120; // the first start may build Nuru's environment
const STOP_SECONDS = 40; // the service gives running work 30 s
const POLL_MS = 500;
const LOG_LINES = 5; // shown when the service exits on its way up

const serviceDir = (home) => path.join(home, ".nuru", "service");
const installPath = (home = os.homedir()) => path.join(home, ".nuru", "install.json");
const logPath = (home = os.homedir()) => path.join(serviceDir(home), "serve.log");
const managedPath = (home) => path.join(serviceDir(home), "managed.json");

const PROBLEMS = {
  "not-installed": "Nuru can't be started from here: the Nuru folder isn't recorded. Run `node plugins/obsidian/install.js <vault>` in it again.",
  "no-pairing": "Nuru couldn't make this plugin's token.",
  exited: "Nuru stopped while starting.",
  slow: "Nuru didn't answer within two minutes of starting.",
  "not-managed": "Nuru was started outside Obsidian, so it can't be switched or restarted from here: do that where it runs.",
  busy: "Nuru is busy with a turn in that vault: try again when it finishes.",
  "not-stopped": "Nuru didn't stop in time.",
  released: "The plugin was turned off while Nuru was starting.",
};

class LaunchProblem extends Error {
  constructor(kind, detail = "") {
    super(PROBLEMS[kind] + (detail ? ` ${detail}` : ""));
    this.kind = kind;
  }
}

// {nuru, uv, path}: the Nuru folder, the uv that install.js found, and the PATH Nuru's tools are found
// on: the installing shell's, else uv's folder in front of Obsidian's own (an older install.json).
function readInstall(home) {
  let recorded;
  try {
    recorded = JSON.parse(fs.readFileSync(installPath(home), "utf8"));
  } catch {
    throw new LaunchProblem("not-installed");
  }
  const { nuru, uv, path: searchPath } = recorded || {};
  if (![nuru, uv].every((p) => typeof p === "string" && path.isAbsolute(p) && fs.existsSync(p))) {
    throw new LaunchProblem("not-installed");
  }
  const fallback = [path.dirname(uv), process.env.PATH].filter(Boolean).join(path.delimiter);
  return { nuru, uv, path: typeof searchPath === "string" && searchPath ? searchPath : fallback };
}

function lastLines(file, n) {
  try {
    return fs.readFileSync(file, "utf8").trimEnd().split("\n").slice(-n).join("\n");
  } catch {
    return "";
  }
}

class Launcher {
  constructor({ vaultPath, port, connect, home = os.homedir(), spawn = childProcess.spawn,
    execFile = childProcess.execFile, fetch = (...args) => globalThis.fetch(...args), pollMs = POLL_MS,
    stopSeconds = STOP_SECONDS }) {
    Object.assign(this, { vaultPath, port, connect, home, spawn, execFile, fetch, pollMs, stopSeconds });
    this.child = null; // the service this plugin started, while it runs
    this.starting = null; // a start under way: a second panel or click waits for it
    this.generation = 0; // release() moves it on, so a start under way gives up
  }

  running() {
    return Boolean(this.child) && this.child.exitCode === null && this.child.signalCode === null;
  }

  // The plugin's token, made the way `nuru-serve token add obsidian` makes it, if there's none yet.
  async pair() {
    if (fs.existsSync(tokenPath(this.home))) return;
    const { nuru, uv, path: searchPath } = readInstall(this.home);
    await new Promise((resolve, reject) => {
      this.execFile(uv, ["run", "--directory", nuru, "nuru-serve", "token", "add", "obsidian"],
        { cwd: nuru, env: { ...process.env, PATH: searchPath }, timeout: START_SECONDS * 1000, windowsHide: true },
        (error) => (error ? reject(new LaunchProblem("no-pairing", error.message)) : resolve()));
    });
  }

  // Start the service for this vault and wait until it answers; {client, status} as connect() gives.
  start() {
    this.starting ??= this.startOnce().finally(() => { this.starting = null; });
    return this.starting;
  }

  async startOnce() {
    const generation = this.generation;
    const released = () => generation !== this.generation;
    const { nuru, uv, path: searchPath } = readInstall(this.home);
    await this.pair();
    if (released()) throw new LaunchProblem("released"); // turned off while pairing: start nothing
    if (!this.running()) {
      fs.mkdirSync(serviceDir(this.home), { recursive: true, mode: 0o700 });
      const log = fs.openSync(logPath(this.home), "a", 0o600);
      try {
        this.child = this.spawn(uv, ["run", "--directory", nuru, "nuru-serve", "--managed", "--port", String(this.port)], {
          cwd: nuru, env: { ...process.env, PATH: searchPath, VAULT_PATH: this.vaultPath }, stdio: ["pipe", log, log],
          windowsHide: true,
        });
      } finally {
        fs.closeSync(log);
      }
      this.child.on("error", () => {}); // a failed spawn shows as an exit below
    }
    const child = this.child;
    const deadline = Date.now() + START_SECONDS * 1000;
    while (Date.now() < deadline) {
      let connected;
      try {
        connected = await this.connect();
      } catch (error) {
        if (!(error instanceof ConnectionProblem) || error.kind !== "no-service") throw error;
      }
      if (released()) throw new LaunchProblem("released"); // release() has closed its pipe
      if (connected) return connected;
      if (child.exitCode !== null || child.signalCode !== null) {
        throw new LaunchProblem("exited", `From ${logPath(this.home)}:\n${lastLines(logPath(this.home), LOG_LINES)}`);
      }
      await new Promise((resolve) => setTimeout(resolve, this.pollMs));
    }
    throw new LaunchProblem("slow", `See ${logPath(this.home)}.`);
  }

  // Stop the service another vault's plugin started, so this vault can have it.
  async stopOther() {
    let managed;
    try {
      managed = JSON.parse(fs.readFileSync(managedPath(this.home), "utf8"));
    } catch {
      throw new LaunchProblem("not-managed");
    }
    const token = fs.readFileSync(tokenPath(this.home), "utf8").trim();
    const url = `http://127.0.0.1:${managed.port}`;
    const response = await this.fetch(`${url}/v1/service/stop`, {
      method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ stop_key: managed.stop_key }),
    });
    if (response.status === 409) throw new LaunchProblem("busy");
    if (response.status !== 202) throw new LaunchProblem("not-managed");
    const deadline = Date.now() + STOP_SECONDS * 1000;
    while (Date.now() < deadline) {
      try {
        await this.fetch(`${url}/v1/status`, { headers: { Authorization: `Bearer ${token}` } });
      } catch {
        return; // nothing listens there any more
      }
      await new Promise((resolve) => setTimeout(resolve, this.pollMs));
    }
    throw new LaunchProblem("not-stopped");
  }

  // Stop the service Obsidian started and start it for this vault again, so it reads its settings
  // (the env files) again. One started from a terminal is left alone: restart it there.
  async restart() {
    const child = this.running() ? this.child : null;
    const exited = child && new Promise((resolve) => child.once("exit", resolve));
    try {
      await this.stopOther();
    } catch (error) {
      if (await this.answering()) throw error; // busy, or started from a terminal
      return this.start(); // nothing runs (managed.json is missing, or left by one that crashed): start it
    }
    if (exited) await this.exitedWithin(exited); // so start() spawns a new one rather than waiting on this one
    return this.start();
  }

  // The stopped service's exit, or "not-stopped" if it hasn't exited within stopSeconds.
  async exitedWithin(exited, seconds = this.stopSeconds) {
    let timer;
    const late = new Promise((resolve, reject) => {
      timer = setTimeout(() => reject(new LaunchProblem("not-stopped")), seconds * 1000);
    });
    try {
      await Promise.race([exited, late]);
    } finally {
      clearTimeout(timer);
    }
  }

  // Whether any service answers on the port, for this vault or not.
  async answering() {
    try {
      await this.connect();
      return true;
    } catch (error) {
      return !(error instanceof ConnectionProblem && error.kind === "no-service");
    }
  }

  // The plugin is going: a service it started stops with it (it sees its stdin close).
  // A start under way gives up, and closes the pipe of a service it goes on to spawn.
  release() {
    this.generation += 1;
    if (this.child?.stdin && !this.child.stdin.destroyed) this.child.stdin.end();
    this.child = null;
  }
}

module.exports = { Launcher, LaunchProblem, installPath, logPath, readInstall };
