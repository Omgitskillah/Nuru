// Removing Nuru from this computer. Setup records here what it added beside Nuru's own install, so
// "everything" means only that: Ollama, if Nuru installed it, and the models Nuru downloaded into it,
// in ~/.nuru/added.json. Everything else in ~/.nuru is Nuru's own: its install, uv, the calculator,
// logs and tokens. This vault's .nuru folder holds its chats and search index; its notes are never touched.
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const ollama = require("./ollama");
const { readInstall } = require("./launcher");
const { run } = require("./setup");

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

// Records that Nuru installed Ollama at `app`, or downloaded `model`.
function remember(home, { app, model }) {
  const now = added(home);
  if (app) now.ollama = app;
  if (model && !now.models.includes(model)) now.models.push(model);
  fs.mkdirSync(path.dirname(addedPath(home)), { recursive: true });
  fs.writeFileSync(addedPath(home), `${JSON.stringify(now, null, 2)}\n`);
}

// Removes the Ollama app at `app`: on Windows with its own uninstaller, silently, which keeps the models
// (Nuru's are deleted before); on macOS by quitting it and deleting the app.
async function removeOllama(app, { platform, runner, base, waitMs = 30 * 1000 }) {
  const gone = async (file) => {
    for (const until = Date.now() + waitMs; fs.existsSync(file);) {
      if (Date.now() > until) throw new Error(`${file} is still there`);
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  };
  if (platform === "win32") {
    const result = await runner(path.join(path.dirname(app), "unins000.exe"), ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], {});
    if (result.code !== 0) throw new Error(`its uninstaller failed (exit ${result.code})`);
    return gone(app);
  }
  for (const name of ["Ollama", "ollama"]) await runner("/usr/bin/pkill", ["-x", name], {});
  for (const until = Date.now() + waitMs; (await ollama.check([], { base })).running;) {
    if (Date.now() > until) throw new Error("it didn't quit");
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return fs.rmSync(app, { recursive: true, force: true });
}

// Deletes everything Nuru added: its models (from an Ollama started for it if Nuru installed one that
// isn't running), Ollama itself if Nuru installed it, uv's download cache, ~/.nuru, and this vault's
// .nuru folder. Nuru must be stopped first. Each part that can't go is said, and the rest still goes.
async function removeEverything({ home = os.homedir(), vault, platform = process.platform, runner = run, base,
  say = () => {}, startOllama = ollama.start, waitMs } = {}) {
  const problems = [];
  const attempt = async (what, work) => {
    try {
      await work();
    } catch (error) {
      problems.push(`${what}: ${error.message}`);
    }
  };
  const { ollama: app, models } = added(home);
  const ours = app && fs.existsSync(app) ? app : null;
  if (models.length) {
    say("Deleting Nuru's models from Ollama…");
    await attempt("Nuru's models are still in Ollama", async () => {
      if (!(await ollama.check([], { base })).running) {
        if (!ours) throw new Error("Ollama isn't running: open it and delete them there, or with `ollama rm`");
        await startOllama(ours, { platform, base, waitMs });
      }
      for (const name of models) await ollama.remove(name, { base });
    });
  }
  if (ours) {
    say("Removing Ollama…");
    await attempt("Ollama is still installed", () => removeOllama(ours, { platform, runner, base, waitMs }));
  }
  let uv = null;
  try {
    ({ uv } = readInstall(home));
  } catch {
    // no install: nothing in uv's cache is Nuru's
  }
  if (uv) {
    say("Clearing uv's download cache…");
    await attempt("uv's download cache is still there", async () => {
      const result = await runner(uv, ["cache", "clean"], {});
      if (result.code !== 0) throw new Error(result.output.trim().split("\n").pop());
    });
  }
  say("Deleting Nuru's install and this vault's Nuru data…");
  for (const dir of [path.join(home, ".nuru"), path.join(vault, ".nuru")]) {
    await attempt(`${dir} is still there`, () => fs.rmSync(dir, { recursive: true, force: true, maxRetries: 3 }));
  }
  return problems;
}

module.exports = { added, remember, removeEverything };
