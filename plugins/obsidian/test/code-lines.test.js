"use strict";
// tools/js_code_lines.js, which tells the coverage gate which plugin lines hold code.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const esbuild = require("esbuild");
const tool = require("../../../tools/js_code_lines.js");

test("regexes, template strings and comments are read as JavaScript, not guessed at", () => {
  const source = [
    "// a header comment",                       // 1
    "const slashOrStar = /[/*]/;",                // 2: a regex that looks like a comment opening
    "function never() {",                         // 3
    '  throw new Error("not run // still code");', // 4
    "}",                                          // 5
    "const note = `one",                          // 6
    "// inside a template string",                // 7
    "three`;",                                    // 8
    "/* a block",                                 // 9
    "   comment */",                              // 10
    "",                                           // 11
    "module.exports = { slashOrStar, never, note };", // 12
  ].join("\n");
  assert.deepEqual(tool.codeLines(source, "a.js", esbuild), [2, 3, 4, 5, 6, 12]);
});

test("source map mappings decode to the source lines they point at", () => {
  // Three segments: line 1 column 0, then down two lines, then down one more (VLQ: C = +1, E = +2).
  assert.deepEqual(tool.mappedLines("AAAA;AAEA,AACA"), [1, 3, 4]);
  assert.deepEqual(tool.mappedLines(";;A"), [], "a segment without a source line maps nothing");
  assert.throws(() => tool.mappedLines("A!AA"), /bad source map segment/);
});

test("the listing covers every source but tests, dependencies and builds, with forward slashes", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-code-lines-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const name of ["main.js", "lib/a.js", "test/a.test.js", "node_modules/x/index.js", "dist/main.js", "lib/notes.md"]) {
    fs.mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
    fs.writeFileSync(path.join(root, name), "x();\n");
  }
  assert.deepEqual(tool.sources(root).sort(), ["lib/a.js", "main.js"]);
  const listing = tool.main(path.join(__dirname, ".."));
  assert.deepEqual(Object.keys(listing).filter((name) => name.startsWith("test/") || name.includes("node_modules")), []);
  assert.ok(listing["lib/client.js"].length > 50 && listing["main.js"].includes(17));
});
