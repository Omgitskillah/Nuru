// Copies a built bundle (dist/) into a vault's .obsidian/plugins/nuru/. Every path is checked before
// anything is written, symlinks are refused, and the files are staged inside the plugin folder and
// renamed into place, the old ones kept aside until all are in: a failed copy or rename leaves the
// installed version as it was. Only the bundle's
// files are touched: data.json (the plugin's settings), the vault's other settings and its notes stay.
// After RemoteNotes' obsidian-plugin/lib/install.js at commit 04a2774ba9c7 (see README).
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const PLUGIN_ID = "nuru"; // manifest.json's id: Obsidian loads a plugin from the folder named after it
const FILES = ["main.js", "manifest.json", "styles.css"];

function checkTarget(target, directory) {
  const stat = fs.lstatSync(target, { throwIfNoEntry: false });
  if (!stat) return;
  if (stat.isSymbolicLink() || (directory ? !stat.isDirectory() : !stat.isFile())) {
    throw new Error(`Refusing unsafe installation path: ${target}`);
  }
}

function installFiles(vault, bundle) {
  const root = fs.realpathSync(vault);
  if (!fs.statSync(root).isDirectory()) throw new Error("Select an existing vault directory.");
  const target = path.join(root, ".obsidian", "plugins", PLUGIN_ID);
  const directories = [path.join(root, ".obsidian"), path.join(root, ".obsidian", "plugins"), target];
  for (const directory of directories) checkTarget(directory, true);
  for (const file of FILES) {
    const source = path.join(bundle, file);
    if (!fs.statSync(source, { throwIfNoEntry: false })?.isFile()) throw new Error(`Missing build file: ${source}`);
    checkTarget(path.join(target, file), false);
  }
  for (const directory of directories) fs.mkdirSync(directory, { recursive: true });
  const stage = fs.mkdtempSync(path.join(target, ".install-"));
  const old = path.join(stage, "old");
  const kept = [];
  const placed = [];
  let keep = false;
  try {
    for (const file of FILES) {
      fs.copyFileSync(path.join(bundle, file), path.join(stage, file));
      fs.chmodSync(path.join(stage, file), 0o644);
    }
    fs.mkdirSync(old);
    for (const file of FILES) {
      if (fs.existsSync(path.join(target, file))) {
        fs.renameSync(path.join(target, file), path.join(old, file));
        kept.push(file);
      }
    }
    for (const file of FILES) {
      fs.renameSync(path.join(stage, file), path.join(target, file));
      placed.push(file);
    }
  } catch (error) {
    // Put the installed version back: remove what was placed, then return what was kept aside. If
    // that fails too, the old files stay in the staging folder and the error says where.
    try {
      for (const file of placed) fs.rmSync(path.join(target, file), { force: true });
      for (const file of kept) fs.renameSync(path.join(old, file), path.join(target, file));
    } catch {
      keep = true;
      error.message += `. The previous version could not be put back; its files are in ${old}`;
    }
    throw error;
  } finally {
    if (!keep) fs.rmSync(stage, { recursive: true, force: true });
  }
  return target;
}

// The uv on PATH, or where its installer puts it. Obsidian started from the Dock doesn't get the
// shell's PATH, so the plugin is given this absolute path (lib/launcher.js).
function findUv(env = process.env, home = os.homedir()) {
  const name = process.platform === "win32" ? "uv.exe" : "uv";
  const dirs = [...(env.PATH || "").split(path.delimiter), path.join(home, ".local", "bin"), path.join(home, ".cargo", "bin"),
    "/opt/homebrew/bin", "/usr/local/bin"];
  return dirs.filter(Boolean).map((dir) => path.join(dir, name)).find((p) => fs.statSync(p, { throwIfNoEntry: false })?.isFile()) || null;
}

// ~/.nuru/install.json: the Nuru folder, uv and the installing shell's PATH, for the plugin to start
// nuru-serve with: Obsidian started from the Dock gets only /usr/bin:/bin:/usr/sbin:/sbin, and Nuru's
// tools run through uv and cargo. Outside every vault, so a vault can't change what the plugin runs.
function recordInstall({ nuru, uv, path: searchPath = process.env.PATH || "", home = os.homedir() }) {
  const dir = path.join(home, ".nuru");
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const file = path.join(dir, "install.json");
  const temporary = `${file}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify({ nuru: fs.realpathSync(nuru), uv, path: searchPath }, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(temporary, file);
  return file;
}

module.exports = { PLUGIN_ID, installFiles, findUv, recordInstall };
