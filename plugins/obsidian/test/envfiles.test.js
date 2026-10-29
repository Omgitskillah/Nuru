"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { NuruSettings, fingerprint, readEnv, setEnv } = require("../lib/envfiles");

// Windows modes have no group or other bits (a file is 0o666 or read-only); the user folder is private there.
const POSIX = process.platform !== "win32";

function folder(files = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-env-"));
  for (const [name, text] of Object.entries(files)) fs.writeFileSync(path.join(dir, name), text, { mode: 0o600 });
  return dir;
}

test("it reads a file as python-dotenv does: the last active line, quotes, comments and export", () => {
  const dir = folder({ ".env": [
    "# A=commented", "A=first", "A=second # why", 'export B="two words # kept"', "C = 'single'", "D=x#y", "E=",
    'F="esc \\"q\\""', ""].join("\n") });
  assert.deepEqual(readEnv(path.join(dir, ".env")),
    { A: "second", B: "two words # kept", C: "single", D: "x#y", E: "", F: 'esc "q"' });
  assert.deepEqual(readEnv(path.join(dir, "missing")), {});
});

test("a write changes only the active line, keeping comments, alternatives, export and its own comment", () => {
  const dir = folder({ ".env": "# The model\n# M=old\nM=a # mine\nexport N=1\nM2=z\n" });
  const file = path.join(dir, ".env");
  setEnv(file, "M", "b c");
  setEnv(file, "N", "2");
  setEnv(file, "NEW", 'say "hi"');
  assert.equal(fs.readFileSync(file, "utf8"), '# The model\n# M=old\nM="b c" # mine\nexport N=2\nM2=z\nNEW="say \\"hi\\""\n');
  assert.deepEqual(readEnv(file), { M: "b c", N: "2", M2: "z", NEW: 'say "hi"' });
  if (POSIX) assert.equal(fs.statSync(file).mode & 0o777, 0o600, "it keeps its permissions");
  assert.deepEqual(fs.readdirSync(dir), [".env"], "no temporary file left");
});

test("a write makes a missing file private, and refuses a line break", () => {
  const dir = folder();
  const file = path.join(dir, ".env.deep-think");
  setEnv(file, "CLAUDE_API_KEY", "sk-ant-new");
  assert.equal(fs.readFileSync(file, "utf8"), "CLAUDE_API_KEY=sk-ant-new\n");
  if (POSIX) assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  assert.throws(() => setEnv(file, "CLAUDE_API_KEY", "a\nINJECTED=1"), /line break/);
  assert.deepEqual(readEnv(file), { CLAUDE_API_KEY: "sk-ant-new" });
});

test("saving the Claude key leaves .env.deep-think readable by its owner only, even if it was world-readable", () => {
  const dir = folder({ ".env.deep-think": "CLAUDE_API_KEY=old\n", ".env.nuru": "NURU_KEEP_ALIVE=5m\n" });
  fs.chmodSync(path.join(dir, ".env.deep-think"), 0o644);
  fs.chmodSync(path.join(dir, ".env.nuru"), 0o644);
  const settings = new NuruSettings(dir, {});
  settings.set("CLAUDE_API_KEY", "sk-ant-new");
  if (POSIX) assert.equal(fs.statSync(path.join(dir, ".env.deep-think")).mode & 0o777, 0o600);
  settings.set("NURU_KEEP_ALIVE", "30m");
  if (POSIX) assert.equal(fs.statSync(path.join(dir, ".env.nuru")).mode & 0o777, 0o644, "a file with no secret keeps its mode");
});

test("each setting is in the file Nuru reads it from", () => {
  const dir = folder({ ".env.nuru": "NURU_BASE_MODEL=qwen3.5:4b\n", ".env.deep-think": "CLAUDE_API_KEY=k\n" });
  const settings = new NuruSettings(dir, {});
  assert.equal(settings.get("NURU_BASE_MODEL"), "qwen3.5:4b");
  assert.equal(settings.get("CLAUDE_API_KEY"), "k");
  settings.set("NURU_KEEP_ALIVE", "-1");
  assert.equal(readEnv(path.join(dir, ".env.nuru")).NURU_KEEP_ALIVE, "-1");
});

test("the default model is Claude when NURU_MAIN_MODEL names it, and choosing a local one switches it back", () => {
  const dir = folder({ ".env.nuru": "NURU_BASE_MODEL=qwen3.5:4b\n" });
  const settings = new NuruSettings(dir, {});
  assert.equal(settings.defaultModel(), "qwen3.5:4b");
  settings.setDefaultModel("anthropic:claude-haiku-5-5");
  assert.equal(settings.defaultModel(), "anthropic:claude-haiku-5-5");
  assert.equal(settings.get("NURU_BASE_MODEL"), "qwen3.5:4b", "the local model stays for later");
  settings.setDefaultModel("granite4.2:3b");
  assert.deepEqual(readEnv(path.join(dir, ".env.nuru")), { NURU_BASE_MODEL: "granite4.2:3b", NURU_MAIN_MODEL: "ollama" });
  assert.equal(settings.defaultModel(), "granite4.2:3b");
});

test("it says when Obsidian's environment has the setting, which Nuru prefers", () => {
  const settings = new NuruSettings(folder(), { ANTHROPIC_API_KEY: "x", NURU_KEEP_ALIVE: "5m" });
  assert.equal(settings.overridden("CLAUDE_API_KEY"), "ANTHROPIC_API_KEY");
  assert.equal(settings.overridden("NURU_KEEP_ALIVE"), "NURU_KEEP_ALIVE");
  assert.equal(settings.overridden("NURU_BASE_MODEL"), null);
});

test("a fingerprint tells keys apart without showing them", () => {
  assert.equal(fingerprint("sk-ant-a").length, 8);
  assert.notEqual(fingerprint("sk-ant-a"), fingerprint("sk-ant-b"));
  assert.ok(!fingerprint("sk-ant-a").includes("sk-ant"));
});

// Failed saves preserve the old file and clean up the private staged replacement.
test("a refused secret-file replacement preserves the original and removes only its staging file", (t) => {
  const dir = folder({ ".env.deep-think": "CLAUDE_API_KEY=old\n", "keep.txt": "unrelated" });
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, ".env.deep-think");
  const failure = Object.assign(new Error("replacement denied"), { code: "EACCES" });
  let staged;
  t.mock.method(fs, "renameSync", (source, target) => {
    assert.equal(target, file);
    staged = source;
    assert.equal(readEnv(source).CLAUDE_API_KEY, "new-secret");
    if (POSIX) assert.equal(fs.statSync(source).mode & 0o777, 0o600);
    throw failure;
  });
  assert.throws(() => setEnv(file, "CLAUDE_API_KEY", "new-secret", { secret: true }), (error) => error === failure);
  assert.equal(fs.readFileSync(file, "utf8"), "CLAUDE_API_KEY=old\n");
  assert.equal(fs.existsSync(staged), false);
  assert.equal(fs.readFileSync(path.join(dir, "keep.txt"), "utf8"), "unrelated");
  assert.deepEqual(fs.readdirSync(dir).sort(), [".env.deep-think", "keep.txt"]);
});

test("an unreadable settings file is not treated as empty or overwritten", (t) => {
  const dir = folder({ ".env.nuru": "NURU_KEEP_ALIVE=30m\n" });
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, ".env.nuru");
  const failure = Object.assign(new Error("read denied"), { code: "EACCES" });
  const originalRead = fs.readFileSync;
  t.mock.method(fs, "readFileSync", (name, ...args) => {
    if (name === file) throw failure;
    return originalRead(name, ...args);
  });
  assert.throws(() => readEnv(file), (error) => error === failure);
  assert.throws(() => setEnv(file, "NURU_KEEP_ALIVE", "-1"), (error) => error === failure);
  assert.equal(originalRead(file, "utf8"), "NURU_KEEP_ALIVE=30m\n");
  assert.deepEqual(fs.readdirSync(dir), [".env.nuru"]);
});
