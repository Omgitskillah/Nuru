// Removing Nuru from this computer, and only what's Nuru's: ~/.nuru (its install, uv, the calculator, logs,
// tokens) and this vault's .nuru folder (its chats and search index); the vault's notes are never touched.
// Setup records in ~/.nuru/added.json what it added beside them: Ollama, if Nuru installed it, and the models
// Nuru downloaded into it. Those aren't deleted from here, since Nuru can't prove which Ollama, or which copy,
// holds them now: they're listed as steps for the user instead.
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

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

// What the user does to remove what Nuru added beside its own files, from added.json.
function steps(home = os.homedir(), platform = process.platform) {
  const { ollama, models } = added(home);
  const list = [];
  if (models.length) {
    list.push(`Delete the models Nuru downloaded, in a terminal: ${models.map((name) => `ollama rm ${name}`).join(", then ")}`);
  }
  if (ollama) {
    list.push(platform === "win32" ? "Uninstall Ollama, which Nuru installed, in Windows Settings → Apps → Installed apps"
      : `Quit Ollama from its menu-bar icon, then move ${ollama}, which Nuru installed, to the Bin`);
  }
  return list;
}

// Deletes ~/.nuru and this vault's .nuru folder; Nuru must be stopped first. {problems}: what couldn't be deleted.
function removeOwn({ home = os.homedir(), vault } = {}) {
  const problems = [];
  for (const dir of [path.join(home, ".nuru"), path.join(vault, ".nuru")]) {
    try {
      fs.rmSync(dir, { recursive: true, force: true, maxRetries: 3 });
    } catch (error) {
      problems.push(`${dir} is still there: ${error.message}`);
    }
  }
  return { problems };
}

module.exports = { added, remember, removeOwn, steps };
