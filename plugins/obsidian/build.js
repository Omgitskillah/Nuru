// Bundles the plugin into dist/ (main.js, manifest.json, styles.css): what goes into a vault's
// .obsidian/plugins/nuru/. Build and test layout after RemoteNotes' obsidian-plugin (see README).
"use strict";

const { buildSync } = require("esbuild");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const { version } = require("./package.json");
const manifest = require("./manifest.json");
if (manifest.version !== version) throw new Error(`manifest.json is ${manifest.version}, package.json ${version}: make them match`);

// A release build (the release workflow) names the bundle it just built in NURU_BUNDLE: "Set up Nuru"
// then downloads exactly that bundle, uv and the calculator, each checked against the sha256 compiled in
// here (lib/setup.js). Any other build can't set Nuru up.
function releaseFacts(bundle) {
  if (path.basename(bundle) !== `nuru-${version}.zip`) throw new Error(`NURU_BUNDLE is ${bundle}, not nuru-${version}.zip`);
  const pins = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "release", "dependencies.json"), "utf8"));
  return {
    version, url: `https://github.com/Omgitskillah/Nuru/releases/download/${version}/`,
    bundle: { asset: path.basename(bundle), sha256: crypto.createHash("sha256").update(fs.readFileSync(bundle)).digest("hex") },
    uv: pins.uv, "math-mcp": pins["math-mcp"],
  };
}
const release = process.env.NURU_BUNDLE ? releaseFacts(process.env.NURU_BUNDLE) : null;

const output = path.join(__dirname, "dist");
fs.mkdirSync(output, { recursive: true });
buildSync({
  entryPoints: [path.join(__dirname, "main.js")], bundle: true, platform: "node", format: "cjs", target: "es2022",
  external: ["obsidian"], outfile: path.join(output, "main.js"), legalComments: "none",
  define: { __NURU_RELEASE__: JSON.stringify(release) },
});
for (const file of ["manifest.json", "styles.css"]) fs.copyFileSync(path.join(__dirname, file), path.join(output, file));
