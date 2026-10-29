"use strict";
// install.js, the developer's `node install.js <vault>`: it builds, installs and records, and stops
// with a clear message when any step can't be done. The build and the steps lib/install.js does
// (tested in install.test.js) are stood in for, so this checks only what the script does with them.
const test = require("node:test");
const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const path = require("node:path");
const install = require("../lib/install");

const SCRIPT = path.join(__dirname, "..", "install.js");

class Exit extends Error {
  constructor(code) { super(`exit ${code}`); this.code = code; }
}

function run(t, { argv = ["/vaults/Notes"], build = { status: 0 }, installed = "/vaults/Notes/.obsidian/plugins/nuru",
  uv = "/usr/local/bin/uv", installFails = null } = {}) {
  const said = { log: [], error: [] }, calls = [];
  const realArgv = process.argv;
  process.argv = [process.execPath, SCRIPT, ...argv];
  t.after(() => { process.argv = realArgv; });
  t.mock.method(process, "exit", (code) => { throw new Exit(code); });
  t.mock.method(console, "log", (line) => said.log.push(line));
  t.mock.method(console, "error", (line) => said.error.push(line));
  t.mock.method(childProcess, "spawnSync", (command, args, options) => { calls.push(["build", command, args, options]); return build; });
  t.mock.method(install, "installFiles", (vault, bundle) => {
    calls.push(["install", vault, bundle]);
    if (installFails) throw new Error(installFails);
    return installed;
  });
  t.mock.method(install, "findUv", () => uv);
  t.mock.method(install, "recordInstall", (record) => { calls.push(["record", record]); return "/home/me/.nuru/install.json"; });
  delete require.cache[SCRIPT];
  let exit = null;
  try {
    require(SCRIPT);
  } catch (error) {
    if (!(error instanceof Exit)) throw error;
    exit = error.code;
  }
  return { said, calls, exit };
}

test("it builds the plugin, installs it into the vault and records where Nuru and uv are", (t) => {
  const { said, calls, exit } = run(t);
  assert.equal(exit, null);
  const [build, installCall, record] = calls;
  assert.deepEqual(build.slice(1, 3), [process.execPath, [path.join(path.dirname(SCRIPT), "build.js")]]);
  assert.equal(build[3].shell, false);
  assert.deepEqual(installCall, ["install", "/vaults/Notes", path.join(path.dirname(SCRIPT), "dist")]);
  assert.deepEqual(record, ["record", { nuru: path.join(path.dirname(SCRIPT), "..", ".."), uv: "/usr/local/bin/uv" }]);
  assert.deepEqual(said.log, ["Installed: /vaults/Notes/.obsidian/plugins/nuru",
    "Recorded for starting Nuru from Obsidian: /home/me/.nuru/install.json",
    "Enable Nuru under Community plugins in this vault, or reload it there if it was already on."]);
});

test("without uv it installs and says how to start Nuru by hand", (t) => {
  const { said, calls } = run(t, { uv: null });
  assert.ok(!calls.some(([kind]) => kind === "record"));
  assert.match(said.log[1], /uv wasn't found.*uv run nuru-serve/);
});

for (const argv of [[], ["/a", "/b"]]) {
  test(`given ${argv.length} vaults it shows how to use it and builds nothing`, (t) => {
    const { said, calls, exit } = run(t, { argv });
    assert.equal(exit, 1);
    assert.deepEqual(said.error, ["Usage: node install.js <existing-vault-path>"]);
    assert.deepEqual(calls, []);
  });
}

for (const [name, build] of [["exits non-zero", { status: 1 }], ["can't start", { error: new Error("ENOENT"), status: null }]]) {
  test(`a build that ${name} stops before installing anything`, (t) => {
    const { said, calls, exit } = run(t, { build });
    assert.equal(exit, 1);
    assert.deepEqual(said.error, ["Plugin build failed."]);
    assert.deepEqual(calls.map(([kind]) => kind), ["build"]);
  });
}

test("an install that can't be done says why and records nothing", (t) => {
  const { said, calls, exit } = run(t, { installFails: "Select an existing vault directory." });
  assert.equal(exit, 1);
  assert.deepEqual(said.error, ["Select an existing vault directory."]);
  assert.ok(!calls.some(([kind]) => kind === "record"));
});
