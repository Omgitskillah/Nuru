// Bundles the plugin into dist/ (main.js, manifest.json, styles.css): what goes into a vault's
// .obsidian/plugins/nuru/. Build and test layout after RemoteNotes' obsidian-plugin (see README).
"use strict";

const { buildSync } = require("esbuild");
const fs = require("node:fs");
const path = require("node:path");

const output = path.join(__dirname, "dist");
fs.mkdirSync(output, { recursive: true });
buildSync({
  entryPoints: [path.join(__dirname, "main.js")], bundle: true, platform: "node", format: "cjs", target: "es2022",
  external: ["obsidian"], outfile: path.join(output, "main.js"), legalComments: "none",
});
for (const file of ["manifest.json", "styles.css"]) fs.copyFileSync(path.join(__dirname, file), path.join(output, file));
