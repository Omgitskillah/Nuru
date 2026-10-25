// Removing Nuru from this computer, and only what's Nuru's. Setup records here what it added beside Nuru's
// own install, so "everything" means only that: Ollama, if Nuru installed it, and the models Nuru downloaded
// into it, in ~/.nuru/added.json. Everything else in ~/.nuru is Nuru's own: its install, uv, the calculator,
// logs and tokens. This vault's .nuru folder holds its chats and search index; its notes are never touched.
// Nothing shared is touched either: uv's download cache, or an Ollama Nuru didn't install.
"use strict";

const childProcess = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const ollama = require("./ollama");

const addedPath = (home) => path.join(home, ".nuru", "added.json");

// {ollama, models}: the Ollama app Nuru installed (its path, or null) and the models it downloaded.
function added(home = os.homedir()) {
  try {
    const { ollama: app = null, models = [] } = JSON.parse(fs.readFileSync(addedPath(home), "utf8"));
    return { ollama: app, models };
  } catch {
    return { ollama: null, models: [] };
  }
}

// Written whole, then moved into place, so a failure part-way never leaves half a record.
function write(home, record) {
  fs.mkdirSync(path.dirname(addedPath(home)), { recursive: true });
  const partial = `${addedPath(home)}.partial`;
  fs.writeFileSync(partial, `${JSON.stringify(record, null, 2)}\n`);
  fs.renameSync(partial, addedPath(home));
}

// Records that Nuru installed Ollama at `app`, or downloaded `model`.
function remember(home, { app, model }) {
  const now = added(home);
  if (app) now.ollama = app;
  if (model && !now.models.includes(model)) now.models.push(model);
  write(home, now);
}

// Every running process as {pid, command}, its program's full path (ps).
function processes() {
  const output = childProcess.execFileSync("/bin/ps", ["-axo", "pid=,comm="], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  return output.split("\n").map((line) => line.trim().match(/^(\d+)\s+(.+)$/)).filter(Boolean)
    .map(([, pid, program]) => ({ pid: Number(pid), command: program }));
}

// Whether `app` is the Ollama.app Nuru installs: exactly ~/Applications/Ollama.app, a folder (not a link),
// holding Ollama's own Info.plist. Nothing else named in added.json is ever stopped or deleted.
function isNurus(app, home) {
  if (app !== path.join(home, "Applications", "Ollama.app")) return false;
  try {
    if (!fs.lstatSync(app).isDirectory()) return false;
    return fs.readFileSync(path.join(app, "Contents", "Info.plist"), "utf8").includes("<string>com.electron.ollama</string>");
  } catch {
    return false;
  }
}

// Removes the Ollama.app Nuru installed (macOS): stops the processes running from inside it, never another
// Ollama, then deletes it.
async function removeOllama(app, { list, kill, waitMs = 30 * 1000 }) {
  const ours = () => list().filter((p) => p.command.startsWith(`${app}/`));
  for (const { pid } of ours()) kill(pid);
  for (const until = Date.now() + waitMs; ours().length;) {
    if (Date.now() > until) throw new Error("it didn't quit");
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  fs.rmSync(app, { recursive: true, force: true });
}

// Deletes everything Nuru added: its models (from the Ollama Nuru installed, started for it if it isn't
// running), that Ollama itself on macOS once the models are gone, ~/.nuru, and this vault's .nuru folder.
// Nuru must be stopped first. Each part that can't go is said, and the rest still goes; what's Nuru's and
// still there stays in ~/.nuru/added.json, so removing Nuru again can finish it. On Windows Ollama is left
// to the user ("manual"): its uninstaller stops every Ollama, and deletes folders any Ollama shares.
// {problems, left, manual}: left is the record kept, or null.
async function removeEverything({ home = os.homedir(), vault, platform = process.platform, list = processes,
  kill = (pid) => process.kill(pid), base, say = () => {}, startOllama = ollama.start, waitMs } = {}) {
  const problems = [];
  const manual = [];
  const { ollama: recorded, models } = added(home);
  const windows = platform === "win32";
  const app = recorded && !windows && isNurus(recorded, home) ? recorded : null;
  if (recorded && windows) manual.push(`Uninstall Ollama, which Nuru installed, in Windows Settings → Apps → Installed apps`);
  else if (recorded && !app) problems.push(`Nuru's record names ${recorded} as its Ollama, but that isn't the Ollama Nuru installs, so it's left alone`);
  const left = { ollama: app, models: [] };
  if (models.length) {
    say("Deleting Nuru's models from Ollama…");
    try {
      if (!(await ollama.check([], { base })).running) {
        if (!app) throw new Error("Ollama isn't running: open it, then remove Nuru again");
        await startOllama(app, { platform, base, waitMs });
      }
      for (const name of models) {
        try {
          await ollama.remove(name, { base });
        } catch (error) {
          left.models.push(name);
          problems.push(`${name} is still in Ollama: ${error.message}`);
        }
      }
    } catch (error) {
      left.models = [...models];
      problems.push(`${models.join(" and ")} ${models.length > 1 ? "are" : "is"} still in Ollama: ${error.message}`);
    }
  }
  if (app && !left.models.length) { // Ollama stays while it still has Nuru's models, so a retry can delete them
    say("Removing Ollama…");
    try {
      await removeOllama(app, { list, kill, waitMs });
      left.ollama = null;
    } catch (error) {
      problems.push(`Ollama is still installed: ${error.message}`);
    }
  }
  say("Deleting Nuru's install and this vault's Nuru data…");
  const nuruDir = path.join(home, ".nuru");
  const keep = left.ollama || left.models.length ? left : null;
  try {
    if (keep) {
      write(home, keep); // first, so the record survives whatever else fails
      for (const name of fs.readdirSync(nuruDir).filter((n) => n !== "added.json")) {
        fs.rmSync(path.join(nuruDir, name), { recursive: true, force: true, maxRetries: 3 });
      }
    } else fs.rmSync(nuruDir, { recursive: true, force: true, maxRetries: 3 });
  } catch (error) {
    problems.push(`${nuruDir} is still there: ${error.message}`);
  }
  try {
    fs.rmSync(path.join(vault, ".nuru"), { recursive: true, force: true, maxRetries: 3 });
  } catch (error) {
    problems.push(`${path.join(vault, ".nuru")} is still there: ${error.message}`);
  }
  return { problems, left: keep, manual };
}

module.exports = { added, processes, remember, removeEverything };
