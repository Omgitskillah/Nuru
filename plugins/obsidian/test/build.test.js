"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { build, releaseFacts } = require("../build");
const { version } = require("../package.json");

const PINS = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "..", "release", "dependencies.json"), "utf8"));

function folder(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "nuru build "));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test("a release build compiles the bundle's exact sha256 and every pin into the plugin", (t) => {
  const dir = folder(t);
  const bundle = path.join(dir, `nuru-${version}.zip`);
  fs.writeFileSync(bundle, "the bundle's bytes");
  const digest = crypto.createHash("sha256").update("the bundle's bytes").digest("hex");

  const facts = build({ output: path.join(dir, "dist"), bundle });

  assert.deepEqual(facts, { version, url: `https://github.com/Omgitskillah/Nuru/releases/download/${version}/`,
    bundle: { asset: `nuru-${version}.zip`, sha256: digest }, uv: PINS.uv, "math-mcp": PINS["math-mcp"], ollama: PINS.ollama });
  const built = fs.readFileSync(path.join(dir, "dist", "main.js"), "utf8");
  for (const value of [digest, facts.url, PINS.uv.url, PINS["math-mcp"].url, PINS.ollama.url,
    ...[PINS.uv, PINS["math-mcp"], PINS.ollama].flatMap((pin) => Object.values(pin.systems).flatMap((s) => [s.asset, s.sha256]))]) {
    assert.ok(built.includes(JSON.stringify(value)), `${value} is in main.js`);
  }
  assert.deepEqual(fs.readdirSync(path.join(dir, "dist")).sort(), ["main.js", "manifest.json", "styles.css"]);
});

test("any other build carries no release, so it can't set Nuru up", (t) => {
  const dir = folder(t);
  assert.equal(build({ output: dir, bundle: "" }), null);
  const built = fs.readFileSync(path.join(dir, "main.js"), "utf8");
  assert.ok(!built.includes(PINS.uv.systems["win32-x64"].sha256));
});

test("a bundle not named for this version, or a manifest at another version, builds nothing", (t) => {
  const dir = folder(t);
  assert.throws(() => releaseFacts(path.join(dir, "nuru-0.0.1.zip")), new RegExp(`not nuru-${version.replace(/\./g, "\\.")}\\.zip`));
  assert.throws(() => build({ output: path.join(dir, "out"), manifestVersion: "9.9.9" }),
    new RegExp(`manifest.json is 9.9.9, package.json ${version.replace(/\./g, "\\.")}: make them match`));
  assert.ok(!fs.existsSync(path.join(dir, "out")));
});

test("run as a script, it builds from NURU_BUNDLE and stops on a wrong one", () => {
  const result = childProcess.spawnSync(process.execPath, [path.join(__dirname, "..", "build.js")],
    { env: { ...process.env, NURU_BUNDLE: "nuru-wrong.zip" }, encoding: "utf8" });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /NURU_BUNDLE is nuru-wrong\.zip/);
});
