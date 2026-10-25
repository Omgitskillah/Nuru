// Ollama, which runs Nuru's local models: whether it answers on this computer, at which version, which
// of the models Nuru needs it has, and downloading one it lacks through its own API. Also installing,
// updating and starting Ollama itself, at the version release/dependencies.json pins and checked against
// its sha256: on Windows its own installer, run silently for this user (it needs no admin rights); on
// macOS its app, unpacked into ~/Applications. An older Ollama in /Applications is left to its own updater.
"use strict";

const childProcess = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const { SetupProblem, download, fileSha256, lock, progress, run } = require("./setup");
const { readZip, writeEntries } = require("./unpack");

const OLLAMA = "http://127.0.0.1:11434";
const DOWNLOAD = "https://ollama.com/download";
// Nuru's own defaults, for an install with no .env.nuru: core/session.py's BASE_MODEL, and the notes
// server's embedding model (servers/obsidian semantic.py's DEFAULT_MODEL), which every install needs.
const CHAT_MODEL = "qwen3.5:4b";
const EMBEDDING_MODEL = "nomic-embed-text:v1.5";

const INSTALL_BYTES = 2 * 1024 * 1024 * 1024; // OllamaSetup.exe is 1.5 GB: NVIDIA's libraries are in it
const START_MS = 2 * 60 * 1000;

// Whether Ollama `found` is `pinned` or newer: "0.40.4" style versions, compared number by number; a
// pre-release ("0.40.4-rc1") comes before its release.
function atLeast(found, pinned) {
  const numbers = (v) => String(v).split(/[.-]/).slice(0, 3).map((n) => parseInt(n, 10) || 0);
  const [a, b] = [numbers(found), numbers(pinned)];
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] > b[i];
  return !String(found).includes("-") || String(pinned).includes("-");
}

// Where Ollama's app is installed on this computer, or null.
function findApp({ platform = process.platform, home = os.homedir(), env = process.env } = {}) {
  const places = platform === "win32"
    ? [path.join(env.LOCALAPPDATA || path.join(home, "AppData", "Local"), "Programs", "Ollama", "ollama app.exe")]
    : ["/Applications/Ollama.app", path.join(home, "Applications", "Ollama.app")];
  return places.find((place) => fs.existsSync(place)) || null;
}

// Waits until Ollama answers, for up to `ms`; `failed()` gives an error that ends the wait early.
async function answering(ms = START_MS, options = {}, failed = () => null) {
  for (const until = Date.now() + ms; ;) {
    try {
      return (await request("GET", "/api/version", options)).version;
    } catch {
      const error = failed();
      if (error) throw new SetupProblem(`Couldn't open Ollama: ${error.message}`);
      if (Date.now() > until) throw new SetupProblem("Ollama didn't start: open it from your applications, then click Check.");
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
}

// Starts the installed app (`app`, from findApp) and waits for it to answer.
async function start(app, { platform = process.platform, spawn = childProcess.spawn, waitMs, base } = {}) {
  const [command, args] = platform === "win32" ? [app, []] : ["/usr/bin/open", [app]];
  let error = null;
  const child = spawn(command, args, { detached: true, stdio: "ignore", shell: false, windowsHide: false });
  child.on("error", (e) => { error = e; }); // gone before it started, say
  child.unref();
  return answering(waitMs, { base }, () => error);
}

// Installs or updates Ollama to `pin` (release/dependencies.json's ollama entry, for `system`), one
// install or setup at a time (the setup lock, across Obsidian windows).
async function install(pin, system, { home = os.homedir(), ...options } = {}) {
  const asset = pin.systems[system];
  if (!asset) throw new SetupProblem(`Nuru can't install Ollama on this computer (${system}): get it from ${DOWNLOAD}.`);
  const nuruDir = path.join(home, ".nuru");
  fs.mkdirSync(nuruDir, { recursive: true });
  const unlock = lock(nuruDir);
  try {
    return await installLocked(pin, asset, home, options);
  } finally {
    unlock();
  }
}

async function installLocked(pin, asset, home, { fetch = download, runner = run, step = () => {}, platform = process.platform,
  spawn, waitMs, base }) {
  const downloading = `Downloading Ollama ${pin.version}…`;
  step(downloading, 0);
  const onProgress = progress(step, downloading);
  if (platform === "win32") {
    const downloads = path.join(home, ".nuru", "downloads");
    fs.mkdirSync(downloads, { recursive: true });
    const installer = await fetch(pin.url + asset.asset, { sha256: asset.sha256, to: path.join(downloads, asset.asset),
      maxBytes: INSTALL_BYTES, timeoutMs: 60 * 60 * 1000, onProgress });
    try {
      // The file run is the file checked: its bytes on disk, just before it runs.
      if ((await fileSha256(installer)) !== asset.sha256) throw new SetupProblem("Ollama's installer changed after it was downloaded.");
      step(`Installing Ollama ${pin.version}…`);
      const result = await runner(installer, ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], {});
      if (result.code !== 0) throw new SetupProblem(`Ollama's installer failed (exit ${result.code}).`);
    } finally {
      fs.rmSync(installer, { force: true });
    }
    step("Waiting for Ollama to start…");
    return answering(waitMs, { base });
  }
  const zip = await fetch(pin.url + asset.asset, { sha256: asset.sha256, maxBytes: INSTALL_BYTES, onProgress });
  step(`Installing Ollama ${pin.version}…`);
  const applications = path.join(home, "Applications");
  const app = path.join(applications, "Ollama.app");
  if (fs.existsSync(app)) throw new SetupProblem(`${app} is already there: open it, or remove it and try again.`);
  fs.mkdirSync(applications, { recursive: true });
  const stage = fs.mkdtempSync(path.join(applications, ".Ollama-"));
  try {
    writeEntries(readZip(zip, "Ollama.app/", { entries: 20000, bytes: INSTALL_BYTES }, { links: true }), path.join(stage, "Ollama.app"));
    fs.renameSync(path.join(stage, "Ollama.app"), app);
  } finally {
    fs.rmSync(stage, { recursive: true, force: true });
  }
  step("Starting Ollama…");
  return start(app, { platform, spawn, waitMs, base });
}

// "name" and "name:latest" are one model to Ollama.
const tagged = (name) => (name.includes(":") ? name : `${name}:latest`);

function request(method, path, { body, base = OLLAMA, timeoutMs = 5000, onLine } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(new URL(path, base), { method, timeout: timeoutMs, headers: body ? { "content-type": "application/json" } : {} }, (response) => {
      let text = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        text += chunk;
        if (!onLine) return;
        const lines = text.split("\n");
        text = lines.pop();
        try {
          for (const line of lines) if (line.trim()) onLine(JSON.parse(line));
        } catch (error) {
          req.destroy();
          reject(error);
        }
      });
      response.on("end", () => {
        if (response.statusCode !== 200) return reject(new Error(`Ollama answered ${response.statusCode}${text ? `: ${text.trim()}` : ""}`));
        if (!onLine) return resolve(JSON.parse(text));
        try {
          if (text.trim()) onLine(JSON.parse(text)); // a last line without its newline
          resolve(null);
        } catch (error) {
          reject(error);
        }
      });
    });
    req.on("timeout", () => req.destroy(new Error("Ollama didn't answer")));
    req.on("error", reject);
    req.end(body ? JSON.stringify(body) : undefined);
  });
}

// {running, version, missing}: the models in `needed` Ollama doesn't have; running false when it
// doesn't answer (not installed, or not started).
async function check(needed, options = {}) {
  let version;
  try {
    ({ version } = await request("GET", "/api/version", options));
  } catch {
    return { running: false, version: null, missing: [...needed] };
  }
  const { models } = await request("GET", "/api/tags", options);
  const have = new Set(models.map((m) => tagged(m.name)));
  return { running: true, version, missing: needed.filter((name) => !have.has(tagged(name))) };
}

// Downloads `name` into Ollama, calling onProgress(completed, total) with the bytes of the part it's
// pulling (total 0 while it does something else: the manifest, checking); done only when Ollama's last
// line says "success". Models are gigabytes, so there is no time limit beyond one between progress lines.
async function pull(name, onProgress = () => {}, options = {}) {
  let finished = false;
  await request("POST", "/api/pull", { ...options, timeoutMs: options.timeoutMs ?? 120 * 1000, body: { model: name, stream: true },
    onLine: (line) => {
      if (line.error) throw new Error(line.error);
      finished = line.status === "success";
      onProgress(line.completed || 0, line.total || 0);
    } });
  if (!finished) throw new Error(`downloading ${name} stopped before Ollama finished it`);
}

module.exports = { CHAT_MODEL, DOWNLOAD, EMBEDDING_MODEL, OLLAMA, atLeast, check, findApp, install, pull, start };
