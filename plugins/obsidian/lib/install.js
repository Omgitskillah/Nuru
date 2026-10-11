// Copies a built bundle (dist/) into a vault's .obsidian/plugins/nuru/. Every path is checked before
// anything is written, symlinks are refused, and the files are staged inside the plugin folder and
// renamed into place, so a failed copy leaves the installed version as it was. Only the bundle's
// files are touched: data.json (the plugin's settings), the vault's other settings and its notes stay.
// After RemoteNotes' obsidian-plugin/lib/install.js at commit 04a2774ba9c7 (see README).
"use strict";

const fs = require("node:fs");
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
  try {
    for (const file of FILES) {
      fs.copyFileSync(path.join(bundle, file), path.join(stage, file));
      fs.chmodSync(path.join(stage, file), 0o644);
    }
    for (const file of FILES) fs.renameSync(path.join(stage, file), path.join(target, file));
  } finally {
    fs.rmSync(stage, { recursive: true, force: true });
  }
  return target;
}

module.exports = { PLUGIN_ID, installFiles };
