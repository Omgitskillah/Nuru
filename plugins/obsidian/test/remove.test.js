"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { added, remember, removeOwn, steps } = require("../lib/remove");

function home(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "nuru remove home "));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test("Nuru records what it added: the Ollama it installed and each model once; nothing recorded is nothing", (t) => {
  const dir = home(t);
  assert.deepEqual(added(dir), { ollama: null, models: [] });
  remember(dir, { model: "a" });
  remember(dir, { model: "a" });
  remember(dir, { app: "/x/Ollama.app" });
  assert.deepEqual(added(dir), { ollama: "/x/Ollama.app", models: ["a"] });
  fs.writeFileSync(path.join(dir, ".nuru", "added.json"), "{broken");
  assert.deepEqual(added(dir), { ollama: null, models: [] });
});

test("what Nuru added to Ollama becomes steps for the user, for macOS and Windows; nothing added, no steps", (t) => {
  const dir = home(t);
  assert.deepEqual(steps(dir, "darwin"), []);
  remember(dir, { model: "qwen3.5:4b" });
  remember(dir, { model: "nomic-embed-text:v1.5" });
  assert.deepEqual(steps(dir, "darwin"), ["Delete the models Nuru downloaded, in a terminal: ollama rm qwen3.5:4b, then ollama rm nomic-embed-text:v1.5"]);
  const app = path.join(dir, "Applications", "Ollama.app");
  remember(dir, { app });
  assert.equal(steps(dir, "darwin")[1], `Quit Ollama from its menu-bar icon, then move ${app}, which Nuru installed, to the Bin`);
  assert.equal(steps(dir, "win32")[1], "Uninstall Ollama, which Nuru installed, in Windows Settings → Apps → Installed apps");
  assert.ok(Array.isArray(steps()), "this computer's own home and system by default");
});

test("only Nuru's own files go: ~/.nuru and the vault's .nuru; the notes, Ollama and anything the record names stay", (t) => {
  const dir = home(t);
  const app = path.join(dir, "Applications", "Ollama.app");
  fs.mkdirSync(app, { recursive: true });
  remember(dir, { app, model: "qwen3.5:4b" });
  fs.mkdirSync(path.join(dir, ".nuru", "app", "1.0.2", "1"), { recursive: true });
  const vault = path.join(dir, "Vault");
  fs.mkdirSync(path.join(vault, ".nuru", "db"), { recursive: true });
  fs.writeFileSync(path.join(vault, "Note.md"), "# mine\n");

  assert.deepEqual(removeOwn({ home: dir, vault }), { problems: [] });
  assert.ok(!fs.existsSync(path.join(dir, ".nuru")) && !fs.existsSync(path.join(vault, ".nuru")));
  assert.ok(fs.existsSync(app), "never another program");
  assert.equal(fs.readFileSync(path.join(vault, "Note.md"), "utf8"), "# mine\n");
});

test("a folder that can't be deleted is said, and the other still goes", (t) => {
  const dir = home(t);
  const vault = path.join(dir, "Vault");
  fs.mkdirSync(path.join(dir, ".nuru"), { recursive: true });
  fs.mkdirSync(path.join(vault, ".nuru"), { recursive: true });
  const rm = fs.rmSync;
  fs.rmSync = (target, options) => {
    if (target === path.join(dir, ".nuru")) throw Object.assign(new Error("EBUSY: resource busy or locked"), { code: "EBUSY" });
    return rm(target, options);
  };
  let result;
  try {
    result = removeOwn({ home: dir, vault });
  } finally {
    fs.rmSync = rm;
  }
  assert.deepEqual(result, { problems: [`${path.join(dir, ".nuru")} is still there: EBUSY: resource busy or locked`] });
  assert.ok(!fs.existsSync(path.join(vault, ".nuru")));
});

test("only model names Ollama itself would write become commands; anything else in the record is never shown", (t) => {
  const dir = home(t);
  for (const model of ["qwen3.5:4b", "library/granite4.2:3b", "qwen3.5:4b; echo UNRELATED_COMMAND", "a\nrm -rf ~", "$(id)", "`id`", "x y",
    "-flag", "a:b:c", "a".repeat(201)]) remember(dir, { model });
  const shown = steps(dir, "darwin");
  assert.deepEqual(shown, ["Delete the models Nuru downloaded, in a terminal: ollama rm qwen3.5:4b, then ollama rm library/granite4.2:3b",
    "Nuru's record names other models it can't show: see `ollama list` for what to delete"]);
  assert.ok(!shown.join("\n").includes("UNRELATED") && !shown.join("\n").includes("rm -rf"));

  fs.writeFileSync(path.join(dir, ".nuru", "added.json"), JSON.stringify({ ollama: "/Users/me/Projects; rm -rf ~", models: [42] }));
  assert.deepEqual(steps(dir, "darwin"), ["Nuru's record names other models it can't show: see `ollama list` for what to delete"],
    "an app anywhere but where Nuru installs it isn't named");
});
