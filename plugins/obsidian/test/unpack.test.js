"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const zlib = require("node:zlib");

const { ArchiveProblem, readTarGz, readZip, writeEntries } = require("../lib/unpack");
const { tarGz, zip } = require("./helpers/archives");

const POSIX = process.platform !== "win32";

function folder(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "nuru unpack "));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

function written(dir) {
  return fs.readdirSync(dir, { recursive: true }).filter((name) => fs.statSync(path.join(dir, name)).isFile())
    .map((name) => name.split(path.sep).join("/")).sort();
}

test("a zip's files under its top folder are written with their contents and execute bit", (t) => {
  const dir = folder(t);
  const archive = zip([
    { name: "nuru-0.1.0/" },
    { name: "nuru-0.1.0/pyproject.toml", data: "[project]\n" },
    { name: "nuru-0.1.0/servers/run.sh", data: "echo hi\n", mode: 0o100755 },
    { name: "nuru-0.1.0/servers/stored.txt", data: "as is", method: 0 },
    { name: "nuru-0.1.0/empty/" },
  ]);

  writeEntries(readZip(archive, "nuru-0.1.0/"), dir);

  assert.deepEqual(written(dir), ["pyproject.toml", "servers/run.sh", "servers/stored.txt"]);
  assert.equal(fs.readFileSync(path.join(dir, "servers", "stored.txt"), "utf8"), "as is");
  if (POSIX) {
    assert.equal(fs.statSync(path.join(dir, "servers", "run.sh")).mode & 0o777, 0o755);
    assert.equal(fs.statSync(path.join(dir, "pyproject.toml")).mode & 0o777, 0o644);
  }
});

test("a .tar.gz's files are written, including a ustar prefix name, and its folders need no entry", (t) => {
  const dir = folder(t);
  const archive = tarGz([
    { name: "uv-aarch64-apple-darwin/", type: "5", mode: 0o755 },
    { name: "uv", data: "binary", mode: 0o755, prefix: "uv-aarch64-apple-darwin" },
    { name: "uv-aarch64-apple-darwin/docs/README", data: "read me" },
  ]);

  writeEntries(readTarGz(archive, "uv-aarch64-apple-darwin/"), dir);

  assert.deepEqual(written(dir), ["docs/README", "uv"]);
  assert.equal(fs.readFileSync(path.join(dir, "uv"), "utf8"), "binary");
  if (POSIX) assert.equal(fs.statSync(path.join(dir, "uv")).mode & 0o777, 0o755);
});

test("an old (v7) tar header is read, its prefix field ignored, and a NUL type is a plain file", () => {
  const entries = readTarGz(tarGz([{ name: "top/a", data: "v7", type: "\0", ustar: false, prefix: "ignored" }]), "top/");
  assert.deepEqual(entries.map((e) => [e.parts, String(e.data)]), [[["a"], "v7"]]);
});

test("a tar header whose size isn't octal is refused", () => {
  for (const sizeField of ["", "12x", "-1"]) {
    assert.throws(() => readTarGz(tarGz([{ name: "top/a", sizeField }]), "top/"), /a damaged tar header/);
  }
});

test("a zip with no top folder is read from its root", () => {
  assert.deepEqual(readZip(zip([{ name: "uv.exe", data: "exe" }]), "").map((e) => e.parts), [["uv.exe"]]);
});

const UNSAFE = ["../escape", "/etc/passwd", "C:/Windows/x", "a\\..\\b", "a/../b", "a/./b", "a//b", "CON", "lpt1.txt",
  "trailing.", "trailing ", "colon:stream", "star*", "control\u0001"];

for (const name of UNSAFE) {
  test(`an entry named ${JSON.stringify(name)} stops the whole archive before anything is written`, (t) => {
    const dir = folder(t);
    const entries = [{ name: "top/first.txt", data: "1" }, { name: `top/${name}`, data: "2" }];
    assert.throws(() => writeEntries(readZip(zip(entries), "top/"), dir), ArchiveProblem);
    assert.throws(() => writeEntries(readTarGz(tarGz(entries), "top/"), dir), ArchiveProblem);
    assert.deepEqual(fs.readdirSync(dir), []);
  });
}

test("an entry outside the expected top folder is refused", () => {
  assert.throws(() => readZip(zip([{ name: "other/x", data: "" }]), "top/"), /"other\/x" isn't under top\//);
});

test("links, devices and anything but plain files and folders are refused", () => {
  assert.throws(() => readZip(zip([{ name: "top/link", data: "/etc", mode: 0o120777 }]), "top/"), /top\/link isn't a plain file/);
  assert.throws(() => readZip(zip([{ name: "top/dir/", mode: 0o120777 }]), "top/"), /top\/dir\/ isn't a plain folder/);
  for (const type of ["1", "2", "3", "4", "6", "x", "L"]) {
    assert.throws(() => readTarGz(tarGz([{ name: "top/entry", type }]), "top/"), /top\/entry isn't a plain file or folder/);
  }
  assert.throws(() => readTarGz(tarGz([{ name: "top/", type: "0", data: "x" }]), "top/"), /isn't a plain file or folder/);
});

test("encrypted entries and unknown compression are refused", () => {
  assert.throws(() => readZip(zip([{ name: "a", data: "x", flags: 1 }]), ""), /a is encrypted or compressed in an unknown way/);
  assert.throws(() => readZip(zip([{ name: "a", data: "x", method: 12 }]), ""), /unknown way/);
});

test("a damaged zip is refused", () => {
  assert.throws(() => readZip(Buffer.from("not a zip at all"), ""), /not a zip file/);
  const good = zip([{ name: "a", data: "x" }]);
  const badDirectory = Buffer.from(good);
  badDirectory.writeUInt32LE(0, badDirectory.length - 22 - 47); // the central header's signature
  assert.throws(() => readZip(badDirectory, ""), /a damaged zip directory/);
  const badLocal = Buffer.from(good);
  badLocal.writeUInt32LE(0, 0);
  assert.throws(() => readZip(badLocal, ""), /a damaged zip entry, a/);
  assert.throws(() => readZip(zip([{ name: "a", data: "xyz", size: 2, method: 0 }]), ""), /a isn't the size the zip says/);
  assert.throws(() => readZip(zip([{ name: "a", data: "xyz", size: 9 }]), ""), /a isn't the size the zip says/);
});

test("a .tar.gz cut short is refused", () => {
  const whole = zlib.gunzipSync(tarGz([{ name: "top/a", data: "x".repeat(600) }]));
  assert.throws(() => readTarGz(zlib.gzipSync(whole.subarray(0, 1024)), "top/"), /top\/a is cut short/);
});

test("more entries or bytes than the limits are refused", () => {
  const limits = { entries: 2, bytes: 10 };
  const three = [{ name: "a", data: "1" }, { name: "b", data: "2" }, { name: "c", data: "3" }];
  assert.throws(() => readZip(zip(three), "", limits), /more than 2 entries/);
  assert.throws(() => readTarGz(tarGz(three), "", { entries: 2, bytes: 1e6 }), /more than 2 entries/);
  assert.throws(() => readZip(zip([{ name: "a", data: "x".repeat(11) }]), "", limits), /more than 10 bytes/);
  assert.throws(() => readTarGz(tarGz([{ name: "a", data: "x".repeat(5000) }]), "", limits), /more than 10 bytes/);
  assert.throws(() => readTarGz(Buffer.from("not gzip"), ""), /not a .tar.gz file/);
});

test("a name twice, or twice in different case on Windows and macOS, is refused rather than overwritten", (t) => {
  const dir = folder(t);
  assert.throws(() => writeEntries(readZip(zip([{ name: "a", data: "1" }, { name: "a", data: "2" }]), ""), dir), { code: "EEXIST" });
  assert.equal(fs.readFileSync(path.join(dir, "a"), "utf8"), "1");
});
