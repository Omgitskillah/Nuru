"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { findUv, installFiles, recordInstall } = require("../lib/install");

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

test("a rename that fails part-way puts the installed version back", (t) => {
  // Review of #16: EPERM on manifest.json's rename left the new main.js beside the old manifest.
  const { vault, bundle } = fixture(t);
  const target = installFiles(vault, bundle);
  for (const file of FILES) fs.writeFileSync(path.join(bundle, file), `${file} 2`);
  const rename = fs.renameSync;
  t.after(() => { fs.renameSync = rename; });
  let failed = false;
  fs.renameSync = (source, destination) => {
    if (!failed && destination === path.join(target, "manifest.json")) {
      failed = true;
      throw Object.assign(new Error("operation not permitted"), { code: "EPERM" });
    }
    rename(source, destination);
  };
  assert.throws(() => installFiles(vault, bundle), /operation not permitted/);
  fs.renameSync = rename;
  for (const file of FILES) assert.equal(fs.readFileSync(path.join(target, file), "utf8"), `${file} 1`);
  assert.deepEqual(fs.readdirSync(target).sort(), [...FILES].sort());
});

test("if the installed version can't be put back either, its files are kept and the error says where", (t) => {
  const { vault, bundle } = fixture(t);
  const target = installFiles(vault, bundle);
  for (const file of FILES) fs.writeFileSync(path.join(bundle, file), `${file} 2`);
  const rename = fs.renameSync;
  t.after(() => { fs.renameSync = rename; });
  fs.renameSync = (source, destination) => {
    if (destination === path.join(target, "manifest.json")) throw new Error("operation not permitted");
    rename(source, destination);
  };
  assert.throws(() => installFiles(vault, bundle), /could not be put back/);
  fs.renameSync = rename;
  const [stage] = fs.readdirSync(target).filter((name) => name.startsWith(".install-"));
  assert.equal(fs.readFileSync(path.join(target, stage, "old", "manifest.json"), "utf8"), "manifest.json 1");
});

test("a failed first install leaves no plugin files behind", (t) => {
  const { vault, bundle } = fixture(t);
  const target = path.join(fs.realpathSync(vault), ".obsidian", "plugins", "nuru");
  const rename = fs.renameSync;
  t.after(() => { fs.renameSync = rename; });
  fs.renameSync = (source, destination) => {
    if (destination === path.join(target, "styles.css")) throw new Error("operation not permitted");
    rename(source, destination);
  };
  assert.throws(() => installFiles(vault, bundle), /operation not permitted/);
  fs.renameSync = rename;
  assert.deepEqual(fs.readdirSync(target), []);
});

test("install.json records the Nuru folder and uv outside the vault, owner-only", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-home-"));
  const bin = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-bin-"));
  fs.writeFileSync(path.join(bin, process.platform === "win32" ? "uv.exe" : "uv"), "");
  const uv = findUv({ PATH: bin }, home);
  assert.equal(uv, path.join(bin, process.platform === "win32" ? "uv.exe" : "uv"));
  const file = recordInstall({ nuru: bin, uv, home });
  assert.equal(file, path.join(home, ".nuru", "install.json"));
  assert.deepEqual(JSON.parse(fs.readFileSync(file, "utf8")), { nuru: fs.realpathSync(bin), uv });
  if (process.platform !== "win32") assert.equal(fs.statSync(file).mode & 0o777, 0o600);
});
