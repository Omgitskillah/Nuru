// For F13's coverage gate (tools/coverage_gate.py): which lines of each plugin source hold code. Node's
// lcov marks comment and blank lines inside code that ran as run, so the gate counts only these lines.
// esbuild parses each file properly (regexes, template strings, comments) and its source map points
// back to every line its output came from; those are the code lines.
//
//   cd plugins/obsidian && node ../../tools/js_code_lines.js > code-lines.json
//
// Writes {"lib/client.js": [1, 3, ...], ...} for every .js file under the current folder, leaving out
// node_modules, dist and test.
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const SKIPPED = new Set(["node_modules", "dist", "test"]);

// The source lines (1-based) a source map's mappings point to.
function mappedLines(mappings) {
  const lines = new Set();
  let line = 0;
  for (const group of mappings.split(";")) {
    for (const segment of group.split(",")) {
      if (!segment) continue;
      const fields = [];
      let value = 0, shift = 0;
      for (const char of segment) {
        const digit = BASE64.indexOf(char);
        if (digit < 0) throw new Error(`bad source map segment ${segment}`);
        value += (digit & 31) << shift;
        if (digit & 32) {
          shift += 5;
        } else {
          fields.push(value & 1 ? -(value >> 1) : value >> 1);
          value = 0;
          shift = 0;
        }
      }
      if (fields.length >= 4) {
        line += fields[2];
        lines.add(line + 1);
      }
    }
  }
  return [...lines].sort((a, b) => a - b);
}

function codeLines(source, name, esbuild) {
  const { map } = esbuild.transformSync(source, { loader: "js", sourcemap: "external", sourcefile: name });
  return mappedLines(JSON.parse(map).mappings);
}

function sources(root, folder = root) {
  return fs.readdirSync(folder, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(folder, entry.name);
    if (entry.isDirectory()) return SKIPPED.has(entry.name) ? [] : sources(root, full);
    return entry.name.endsWith(".js") ? [path.relative(root, full).split(path.sep).join("/")] : [];
  });
}

function main(root = process.cwd()) {
  const esbuild = require(require.resolve("esbuild", { paths: [root] }));
  const result = {};
  for (const name of sources(root).sort()) result[name] = codeLines(fs.readFileSync(path.join(root, name), "utf8"), name, esbuild);
  return result;
}

if (require.main === module) process.stdout.write(`${JSON.stringify(main(), null, 1)}\n`);

module.exports = { mappedLines, codeLines, sources, main };
