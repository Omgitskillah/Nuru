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

function write(home, record) {
  fs.mkdirSync(path.dirname(addedPath(home)), { recursive: true });
  fs.writeFileSync(addedPath(home), `${JSON.stringify(record, null, 2)}\n`);
}

// Records that Nuru installed Ollama at `app`, or downloaded `model`.
function remember(home, { app, model }) {
  const now = added(home);
  if (app) now.ollama = app;
  if (model && !now.models.includes(model)) now.models.push(model);
  write(home, now);
}

// Every running process as {pid, command}, its program's full path: ps on macOS, Windows' process list there.
const LIST = {
  win32: ["powershell.exe", ["-NoProfile", "-NonInteractive", "-Command",
    "Get-CimInstance Win32_Process | Where-Object ExecutablePath | ForEach-Object { \"$($_.ProcessId) $($_.ExecutablePath)\" }"]],
  other: ["/bin/ps", ["-axo", "pid=,comm="]],
};

function processes(platform = process.platform) {
  const [command, args] = LIST[platform] || LIST.other;
  const output = childProcess.execFileSync(command, args, { encoding: "utf8", maxBuffer: 64 * 1024 * 1024, windowsHide: true });
  return output.split(/\r?\n/).map((line) => line.trim().match(/^(\d+)\s+(.+)$/)).filter(Boolean)
    .map(([, pid, program]) => ({ pid: Number(pid), command: program }));
}

const inside = (dir, file, platform) => (platform === "win32"
  ? file.toLowerCase().startsWith(`${dir.toLowerCase()}\\`) : file.startsWith(`${dir}/`));

// Removes the Ollama app at `app`, never another Ollama: on Windows with its own uninstaller, silently, which
// keeps the models (Nuru's are deleted before), and only while no Ollama from elsewhere runs, since the
// uninstaller stops Ollama by name; on macOS by stopping the processes running from inside the app, then
// deleting it.
async function removeOllama(app, { platform, runner, list, kill, waitMs = 30 * 1000 }) {
  const wait = async (done, problem) => {
    for (const until = Date.now() + waitMs; !done();) {
      if (Date.now() > until) throw new Error(problem);
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  };
  if (platform === "win32") {
    const dir = path.dirname(app);
    const others = list(platform).filter((p) => /(^|\\)ollama( app)?\.exe$/i.test(p.command) && !inside(dir, p.command, platform));
    if (others.length) throw new Error(`another Ollama is running (${others[0].command}): quit it, then remove Nuru again`);
    const result = await runner(path.join(dir, "unins000.exe"), ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], {});
    if (result.code !== 0) throw new Error(`its uninstaller failed (exit ${result.code})`);
    return wait(() => !fs.existsSync(app), `${app} is still there`);
  }
  const ours = () => list(platform).filter((p) => inside(app, p.command, platform));
  for (const { pid } of ours()) kill(pid);
  await wait(() => !ours().length, "it didn't quit");
  return fs.rmSync(app, { recursive: true, force: true });
}

// Deletes everything Nuru added: its models (from an Ollama started for it if Nuru installed one that isn't
// running), Ollama itself if Nuru installed it, ~/.nuru, and this vault's .nuru folder. Nuru must be stopped
// first. Each part that can't go is said, and the rest still goes; what's Nuru's and still there stays in
// ~/.nuru/added.json, so removing Nuru again can finish it. {problems, left}: left is that record, or null.
async function removeEverything({ home = os.homedir(), vault, platform = process.platform, runner = require("./setup").run,
  list = processes, kill = (pid) => process.kill(pid), base, say = () => {}, startOllama = ollama.start, waitMs } = {}) {
  const problems = [];
  const { ollama: app, models } = added(home);
  const left = { ollama: app && fs.existsSync(app) ? app : null, models: [] };
  if (models.length) {
    say("Deleting Nuru's models from Ollama…");
    try {
      if (!(await ollama.check([], { base })).running) {
        if (!left.ollama) throw new Error("Ollama isn't running: open it, then remove Nuru again");
        await startOllama(left.ollama, { platform, base, waitMs });
      }
    } catch (error) {
      left.models = [...models];
      problems.push(`${models.join(" and ")} ${models.length > 1 ? "are" : "is"} still in Ollama: ${error.message}`);
    }
    for (const name of left.models.length ? [] : models) {
      try {
        await ollama.remove(name, { base });
      } catch (error) {
        left.models.push(name);
        problems.push(`${name} is still in Ollama: ${error.message}`);
      }
    }
  }
  if (left.ollama) {
    say("Removing Ollama…");
    try {
      await removeOllama(left.ollama, { platform, runner, list, kill, waitMs });
      left.ollama = null;
    } catch (error) {
      problems.push(`Ollama is still installed: ${error.message}`);
    }
  }
  say("Deleting Nuru's install and this vault's Nuru data…");
  const nuruDir = path.join(home, ".nuru");
  const keep = left.ollama || left.models.length ? left : null;
  for (const dir of [nuruDir, path.join(vault, ".nuru")]) {
    try {
      if (dir === nuruDir && keep && fs.existsSync(dir)) {
        for (const name of fs.readdirSync(dir)) fs.rmSync(path.join(dir, name), { recursive: true, force: true, maxRetries: 3 });
        write(home, keep);
      } else fs.rmSync(dir, { recursive: true, force: true, maxRetries: 3 });
    } catch (error) {
      problems.push(`${dir} is still there: ${error.message}`);
    }
  }
  return { problems, left: keep };
}

module.exports = { added, processes, remember, removeEverything };
