// Builds the plugin and installs it into an existing vault: node install.js <vault>. It never
// enables the plugin, and never touches the plugin's settings or the notes (lib/install.js).
// After RemoteNotes' obsidian-plugin/install.js at commit 04a2774ba9c7 (see README).
"use strict";

const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { findUv, installFiles, recordInstall } = require("./lib/install");

if (process.argv.length !== 3) {
  console.error("Usage: node install.js <existing-vault-path>");
  process.exit(1);
}
const result = spawnSync(process.execPath, [path.join(__dirname, "build.js")], { cwd: __dirname, stdio: "inherit", shell: false });
if (result.error || result.status !== 0) {
  console.error("Plugin build failed.");
  process.exit(1);
}
try {
  console.log(`Installed: ${installFiles(process.argv[2], path.join(__dirname, "dist"))}`);
  const uv = findUv();
  if (uv) {
    console.log(`Recorded for starting Nuru from Obsidian: ${recordInstall({ nuru: path.join(__dirname, "..", ".."), uv })}`);
  } else {
    console.log("uv wasn't found, so the plugin can't start Nuru by itself: start it with `uv run nuru-serve`.");
  }
  console.log("Enable Nuru under Community plugins in this vault, or reload it there if it was already on.");
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
