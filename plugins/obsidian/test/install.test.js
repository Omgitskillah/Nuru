"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { installFiles } = require("../lib/install");

const FILES = ["main.js", "manifest.json", "styles.css"];

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-install-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const vault = path.join(root, "vault");
  const bundle = path.join(root, "bundle");
  fs.mkdirSync(vault); fs.mkdirSync(bundle);
  for (const file of FILES) fs.writeFileSync(path.join(bundle, file), `${file} 1`);
  const notAVault = path.join(root, "file"); fs.writeFileSync(notAVault, "a file");
  return { root, vault, bundle, notAVault };
}

test("install and upgrade keep the notes, the plugin's settings and which plugins are on", (t) => {
  const { vault, bundle } = fixture(t);
  fs.writeFileSync(path.join(vault, "Note.md"), "user text");
  const target = installFiles(vault, bundle);
  assert.equal(target, path.join(fs.realpathSync(vault), ".obsidian", "plugins", "nuru"));
  assert.deepEqual(fs.readdirSync(target).sort(), FILES);
  fs.writeFileSync(path.join(target, "data.json"), '{"port":27125}');
  const enabled = path.join(vault, ".obsidian", "community-plugins.json");
  fs.writeFileSync(enabled, '["nuru"]');
  for (const file of FILES) fs.writeFileSync(path.join(bundle, file), `${file} 2`);

  assert.equal(installFiles(vault, bundle), target);
  for (const file of FILES) assert.equal(fs.readFileSync(path.join(target, file), "utf8"), `${file} 2`);
  assert.equal(fs.readFileSync(path.join(target, "data.json"), "utf8"), '{"port":27125}');
  assert.equal(fs.readFileSync(enabled, "utf8"), '["nuru"]');
  assert.equal(fs.readFileSync(path.join(vault, "Note.md"), "utf8"), "user text");
  assert.ok(!fs.readdirSync(target).some((name) => name.startsWith(".install-")));
});

test("a missing build file or a vault that isn't a folder fails before anything is written", (t) => {
  const { root, vault, bundle, notAVault } = fixture(t);
  assert.throws(() => installFiles(notAVault, bundle), /vault directory/);
  assert.throws(() => installFiles(path.join(root, "missing"), bundle));
  fs.unlinkSync(path.join(bundle, "styles.css"));
  assert.throws(() => installFiles(vault, bundle), /Missing build file/);
  assert.deepEqual(fs.readdirSync(vault), []);
  fs.mkdirSync(path.join(bundle, "styles.css"));
  assert.throws(() => installFiles(vault, bundle), /Missing build file/);
});

test("symlinked folders, dangling links and folders in a file's place are refused", (t) => {
  const { root, vault, bundle } = fixture(t);
  const outside = path.join(root, "outside"); fs.mkdirSync(outside);
  fs.symlinkSync(outside, path.join(vault, ".obsidian"), "dir");
  assert.throws(() => installFiles(vault, bundle), /unsafe/);
  assert.deepEqual(fs.readdirSync(outside), []);
  fs.unlinkSync(path.join(vault, ".obsidian"));

  const target = installFiles(vault, bundle);
  const main = path.join(target, "main.js");
  fs.unlinkSync(main);
  fs.symlinkSync(path.join(outside, "missing"), main);
  assert.throws(() => installFiles(vault, bundle), /unsafe/);
  assert.ok(!fs.existsSync(path.join(outside, "missing")));
  fs.unlinkSync(main); fs.mkdirSync(main);
  assert.throws(() => installFiles(vault, bundle), /unsafe/);
});

test("a failed copy leaves the installed version and no staging folder", (t) => {
  const { vault, bundle } = fixture(t);
  const target = installFiles(vault, bundle);
  for (const file of FILES) fs.writeFileSync(path.join(bundle, file), `${file} 2`);
  const copy = fs.copyFileSync;
  t.after(() => { fs.copyFileSync = copy; });
  fs.copyFileSync = (source, destination) => {
    if (source === path.join(bundle, "styles.css")) throw new Error("disk failure");
    copy(source, destination);
  };
  assert.throws(() => installFiles(vault, bundle), /disk failure/);
  fs.copyFileSync = copy;
  for (const file of FILES) assert.equal(fs.readFileSync(path.join(target, file), "utf8"), `${file} 1`);
  assert.ok(!fs.readdirSync(target).some((name) => name.startsWith(".install-")));
});
