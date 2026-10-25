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

// {ollama, models}: the Ollama app Nuru installed (its path, or null) and the models it downloaded. The file
// can be edited, so anything of another shape reads as nothing: a path that isn't a string, models that
// aren't strings in a list.
function added(home = os.homedir()) {
  let record;
  try {
    record = JSON.parse(fs.readFileSync(addedPath(home), "utf8"));
  } catch {
    record = null;
  }
  const { ollama, models } = record && typeof record === "object" ? record : {};
  return { ollama: typeof ollama === "string" ? ollama : null,
    models: Array.isArray(models) ? models.filter((name) => typeof name === "string") : [] };
}

// Records that Nuru installed Ollama at `app`, or downloaded `model`.
function remember(home, { app, model }) {
  const now = added(home);
  if (app) now.ollama = app;
  if (model && !now.models.includes(model)) now.models.push(model);
  fs.mkdirSync(path.dirname(addedPath(home)), { recursive: true });
  fs.writeFileSync(addedPath(home), `${JSON.stringify(now, null, 2)}\n`);
}

// An Ollama model name, as Ollama writes them ("qwen3.5:4b", "library/name:tag"): nothing a shell reads as
// more than one word. added.json can be edited, so only such names become commands to copy.
const MODEL_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*(\/[A-Za-z0-9][A-Za-z0-9._-]*)*(:[A-Za-z0-9][A-Za-z0-9._-]*)?$/;

// What the user does to remove what Nuru added beside its own files, from added.json. The Ollama app is
// named only where Nuru installs it on macOS; on Windows, Settings finds it.
function steps(home = os.homedir(), platform = process.platform) {
  const { ollama, models } = added(home);
  const names = models.filter((name) => name.length <= 200 && MODEL_NAME.test(name));
  const list = [];
  if (names.length) list.push(`Delete the models Nuru downloaded, in a terminal: ${names.map((name) => `ollama rm ${name}`).join(", then ")}`);
  if (names.length < models.length) list.push("Nuru's record names other models it can't show: see `ollama list` for what to delete");
  if (ollama && platform === "win32") list.push("Uninstall Ollama, which Nuru installed, in Windows Settings → Apps → Installed apps");
  else if (ollama === path.join(home, "Applications", "Ollama.app")) {
    list.push(`Quit Ollama from its menu-bar icon, then move ${ollama}, which Nuru installed, to the Bin`);
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
