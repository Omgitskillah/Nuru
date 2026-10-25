"use strict";

require("./helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const obsidian = require("obsidian");

const { readEnv } = require("../lib/envfiles");
const { NuruSettingTab } = require("../lib/settings");
const { until } = require("./helpers/setup");

const KEY = "sk-ant-api03-old";

function tab({ connect, running = false } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-settings-"));
  const nuru = path.join(home, "Nuru");
  const uv = path.join(home, "uv");
  fs.mkdirSync(path.join(home, ".nuru"), { recursive: true });
  fs.mkdirSync(nuru);
  fs.writeFileSync(uv, "");
  fs.writeFileSync(path.join(home, ".nuru", "install.json"), JSON.stringify({ nuru, uv, path: "/usr/bin" }));
  fs.writeFileSync(path.join(nuru, ".env.nuru"), "# NURU_BASE_MODEL=granite4.2:3b\nNURU_BASE_MODEL=qwen3.5:4b\n");
  fs.writeFileSync(path.join(nuru, ".env.deep-think"), `CLAUDE_API_KEY=${KEY}\n`, { mode: 0o600 });
  const restarts = [];
  const reconnects = [];
  const plugin = {
    settings: { port: 27125, autoStart: true }, remember: async () => {}, vaultPath: () => home,
    launcher: { home, running: () => running, restart: async () => { restarts.push(1); return {}; } },
    views: () => [{ reconnect: async () => reconnects.push(1) }],
    connect: connect || (async () => ({ status: { models: { default: "qwen3.5:4b", choices: [
      { id: "qwen3.5:4b", label: "qwen3.5:4b", cloud: false }, { id: "granite4.2:3b", label: "granite4.2:3b", cloud: false },
      { id: "anthropic:claude-haiku-5-5", label: "Claude Haiku 5.5", cloud: true }] } } })),
  };
  const settingTab = new NuruSettingTab({}, plugin);
  settingTab.ollama = fakeOllama();
  settingTab.release = { version: "1.0.1", ollama: OLLAMA_PIN };
  settingTab.system = "darwin-arm64";
  return { settingTab, nuru, home, restarts, reconnects };
}

// Stands in for lib/ollama.js. `have` are the models it has, and `version` the Ollama running (null when
// none is); `app` is where findApp finds it installed. install and start make it run at the pinned version.
function fakeOllama({ have = ["qwen3.5:4b", "nomic-embed-text:v1.5"], version = "0.40.4", app = null, pull, install } = {}) {
  const real = require("../lib/ollama");
  const fake = {
    ...real, have, version, app, asked: [], pulled: [], installed: [], started: [],
    check: async (needed) => {
      fake.asked.push(needed);
      return { running: Boolean(fake.version), version: fake.version, missing: needed.filter((m) => !fake.have.includes(m)) };
    },
    findApp: () => fake.app,
    pull: pull || (async (name, onProgress) => { onProgress(50, 100); fake.pulled.push(name); fake.have.push(name); }),
    install: install || (async (pin, system, { step }) => {
      step(`Installing Ollama ${pin.version}…`);
      fake.installed.push([pin.version, system]);
      fake.version = pin.version;
      fake.app = system.startsWith("win32") ? "C:/Programs/Ollama/ollama app.exe" : "/Users/me/Applications/Ollama.app";
    }),
    start: async (where) => { fake.started.push(where); fake.version = "0.40.4"; },
  };
  return fake;
}

const OLLAMA_PIN = { version: "0.40.4", url: "https://github.com/ollama/ollama/releases/download/v0.40.4/",
  systems: { "darwin-arm64": { asset: "Ollama-darwin.zip", sha256: "a".repeat(64) }, "win32-x64": { asset: "OllamaSetup.exe", sha256: "b".repeat(64) } } };

const row = (settingTab, name) => [...settingTab.containerEl.children].find((el) => el.firstChild?.textContent === name);
const change = (el, value) => { el.value = value; el.dispatchEvent(new window.Event(el.tagName === "SELECT" ? "change" : "input")); };

test("the Claude key shows only its fingerprint, and goes to Anthropic only when Check is clicked", async (t) => {
  const asked = [];
  obsidian.requestUrl.handler = async (request) => { asked.push(request.headers["x-api-key"]); return { status: 401 }; };
  t.after(() => { obsidian.requestUrl.handler = async () => ({ status: 200 }); });
  const { settingTab, nuru } = tab();
  settingTab.display();
  const key = row(settingTab, "Claude API key");
  const button = (text) => [...key.querySelectorAll("button")].find((b) => b.textContent === text);
  assert.match(key.textContent, /fingerprint [0-9a-f]{8}\)\./);
  assert.ok(!settingTab.containerEl.innerHTML.includes(KEY), "the key itself is never on the page");
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.deepEqual(asked, [], "opening the tab sends nothing");

  button("Check").click();
  await until(() => /Anthropic rejects it: paste a new one/.test(key.textContent));
  assert.deepEqual(asked, [KEY]);

  obsidian.requestUrl.handler = async (request) => { asked.push(request.headers["x-api-key"]); return { status: 200 }; };
  const input = key.querySelector("input");
  assert.equal(input.type, "password");
  change(input, " sk-ant-api03-new ");
  button("Save").click();
  assert.equal(readEnv(path.join(nuru, ".env.deep-think")).CLAUDE_API_KEY, "sk-ant-api03-new");
  assert.equal(input.value, "", "the field is cleared once it's saved");
  assert.doesNotMatch(key.textContent, /rejects it/, "the old key's result goes with it");
  assert.equal(asked.length, 1, "saving sends nothing");
  assert.match(row(settingTab, "Apply changes").textContent, /restart Nuru to use it/);

  button("Check").click();
  await until(() => /accepts it/.test(key.textContent));
  assert.equal(asked.at(-1), "sk-ant-api03-new");
});

test("a check that answers after another key is saved shows nothing for the new key", async (t) => {
  let answer;
  obsidian.requestUrl.handler = () => new Promise((resolve) => { answer = resolve; });
  t.after(() => { obsidian.requestUrl.handler = async () => ({ status: 200 }); });
  const { settingTab } = tab();
  settingTab.display();
  const key = row(settingTab, "Claude API key");
  const button = (text) => [...key.querySelectorAll("button")].find((b) => b.textContent === text);
  button("Check").click();
  await until(() => answer);
  change(key.querySelector("input"), "sk-ant-api03-new");
  button("Save").click();
  answer({ status: 401 });
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.doesNotMatch(key.textContent, /rejects it|Checking/);
});

test("the default model is chosen from the models Nuru lists, and written to .env.nuru", async () => {
  const { settingTab, nuru } = tab();
  settingTab.display();
  const select = row(settingTab, "Default model").querySelector("select");
  await until(() => select.options.length === 3);
  assert.equal(select.value, "qwen3.5:4b");
  assert.deepEqual([...select.options].map((o) => o.textContent), ["qwen3.5:4b", "granite4.2:3b", "Claude Haiku 5.5 (cloud)"]);
  change(select, "anthropic:claude-haiku-5-5");
  assert.equal(readEnv(path.join(nuru, ".env.nuru")).NURU_MAIN_MODEL, "anthropic:claude-haiku-5-5");
  change(select, "granite4.2:3b");
  assert.deepEqual(readEnv(path.join(nuru, ".env.nuru")), { NURU_BASE_MODEL: "granite4.2:3b", NURU_MAIN_MODEL: "ollama" });
  assert.match(fs.readFileSync(path.join(nuru, ".env.nuru"), "utf8"), /^# NURU_BASE_MODEL=granite4.2:3b\n/, "comments stay");
});

test("without Nuru running the current model still shows; keep-alive and restart work", async () => {
  const { settingTab, nuru, restarts, reconnects } = tab({ connect: async () => { throw new Error("no service"); } });
  settingTab.display();
  const model = row(settingTab, "Default model");
  await until(() => /Start Nuru to choose/.test(model.textContent));
  assert.equal(model.querySelector("select").value, "qwen3.5:4b");
  const keepAlive = row(settingTab, "Keep the local model loaded").querySelector("select");
  assert.equal(keepAlive.value, "30m", "Nuru's default when it isn't set");
  change(keepAlive, "-1");
  assert.equal(readEnv(path.join(nuru, ".env.nuru")).NURU_KEEP_ALIVE, "-1");
  const apply = row(settingTab, "Apply changes");
  apply.querySelector("button").click();
  await until(() => /Restarted/.test(apply.textContent));
  assert.deepEqual([restarts.length, reconnects.length], [1, 1]);
});

const { SetupProblem } = require("../lib/setup");
const { added, remember } = require("../lib/remove");
const { LaunchProblem } = require("../lib/launcher");

const buttonOf = (settingTab, name, text) => [...row(settingTab, name).querySelectorAll("button")].find((b) => b.textContent === text);
const said = (settingTab, name) => row(settingTab, name).textContent;
const shown = async (settingTab) => { await settingTab.stepsChecked; };

test("with Nuru not set up, only step 1 can run; Set up Nuru installs it, and the tab then shows Nuru's settings", async () => {
  const { settingTab, home, nuru, restarts } = tab();
  fs.rmSync(path.join(home, ".nuru", "install.json"));
  const calls = [];
  settingTab.setUp = async (options) => {
    calls.push([options.home, options.start]);
    options.step("Downloading Nuru…");
    fs.writeFileSync(path.join(home, ".nuru", "install.json"), JSON.stringify({ nuru, uv: path.join(home, "uv") }));
    return { version: "1.0.1", nuru };
  };
  settingTab.display();
  await shown(settingTab);
  assert.equal(row(settingTab, "Default model"), undefined);
  assert.match(said(settingTab, "1. Nuru"), /Downloads Nuru and what it needs .* Your notes aren't touched/);
  assert.match(said(settingTab, "2. Ollama"), /After step 1\./);
  assert.equal(buttonOf(settingTab, "2. Ollama", "Install Ollama").disabled, true);
  assert.match(said(settingTab, "3. Models"), /After step 2|✓/);
  assert.match(said(settingTab, "4. Ready"), /After the steps above/);

  buttonOf(settingTab, "1. Nuru", "Set up Nuru").click();
  await until(() => row(settingTab, "1. Nuru") && /Nuru 1\.0\.1 is set up\./.test(said(settingTab, "1. Nuru")));

  assert.deepEqual(calls, [[home, null]], "nothing was running, so no restart");
  assert.ok(row(settingTab, "Default model"), "Nuru's own settings are shown now");
  assert.deepEqual(restarts, []);
  await shown(settingTab);
  assert.match(said(settingTab, "4. Ready"), /✓ Open the Nuru panel to chat\./);
  settingTab.display();
  await shown(settingTab);
  assert.match(said(settingTab, "1. Nuru"), /✓ Nuru is set up\. Repair installs Nuru 1\.0\.1 again/, "the news is shown once");
});

test("a repair while Nuru runs restarts it on the new install and reconnects the chat", async () => {
  const { settingTab, restarts, reconnects } = tab({ running: true });
  settingTab.setUp = async ({ start }) => { await start(); return { version: "1.0.1" }; };
  settingTab.display();
  await shown(settingTab);
  buttonOf(settingTab, "1. Nuru", "Repair").click();
  await until(() => restarts.length && /is set up/.test(said(settingTab, "1. Nuru")));
  assert.deepEqual([restarts.length, reconnects.length], [1, 1]);
});

test("what went wrong in a setup, its restart or the rollback after it, is said in step 1", async () => {
  for (const [running, setUp, words] of [
    [false, async () => { throw new SetupProblem("Couldn't download uv: the server answered 404"); }, "Couldn't download uv: the server answered 404"],
    [false, async () => { throw new Error("EACCES"); }, "Setting Nuru up failed: EACCES"],
    [true, async () => ({ version: "0.2.0", rolledBack: true, startError: new LaunchProblem("exited") }),
      "Nuru 0.2.0 was set up, but it didn't start: Nuru stopped while starting. Nuru is back on the install it had before."],
    [true, async () => ({ version: "0.2.0", startError: new LaunchProblem("exited"), rollbackError: new LaunchProblem("exited") }),
      "Nuru 0.2.0 was set up, but it didn't start: Nuru stopped while starting. Going back to the install it had before failed too: Nuru stopped while starting."],
  ]) {
    const { settingTab } = tab({ running });
    settingTab.setUp = setUp;
    settingTab.display();
    await shown(settingTab);
    buttonOf(settingTab, "1. Nuru", "Repair").click();
    await until(() => said(settingTab, "1. Nuru").includes(words));
  }
});

test("with Ollama at the pinned version and the models there, every step shows done and nothing is left to click", async () => {
  const { settingTab } = tab();
  settingTab.display();
  await shown(settingTab);
  assert.match(said(settingTab, "2. Ollama"), /✓ Ollama 0\.40\.4 is running\./);
  assert.match(said(settingTab, "3. Models"), /✓ Ollama has the models Nuru needs: qwen3\.5:4b and nomic-embed-text:v1\.5\./);
  assert.match(said(settingTab, "4. Ready"), /✓ Open the Nuru panel to chat\./);
  assert.deepEqual(settingTab.ollama.asked, [["qwen3.5:4b", "nomic-embed-text:v1.5"]]);
  for (const [name, text] of [["2. Ollama", "Install Ollama"], ["3. Models", "Download"], ["Set up everything", "Set up everything"]]) {
    assert.equal(buttonOf(settingTab, name, text).disabled, true, `${text} is off`);
  }
});

test("a missing Ollama is installed at the pinned version, then the models download, with progress", async () => {
  const { settingTab, home } = tab();
  settingTab.ollama = fakeOllama({ version: null, have: [] });
  settingTab.display();
  await shown(settingTab);
  assert.match(said(settingTab, "2. Ollama"), /Install Ollama downloads and installs Ollama 0\.40\.4 for you \(200 MB\)\./);
  assert.match(said(settingTab, "3. Models"), /After step 2\./);
  assert.equal(buttonOf(settingTab, "3. Models", "Download").disabled, true);

  buttonOf(settingTab, "2. Ollama", "Install Ollama").click();
  await until(() => /✓ Ollama 0\.40\.4 is running/.test(said(settingTab, "2. Ollama")));
  assert.deepEqual(settingTab.ollama.installed, [["0.40.4", "darwin-arm64"]]);
  assert.match(said(settingTab, "3. Models"), /doesn't have qwen3\.5:4b and nomic-embed-text:v1\.5 yet: click Download/);

  buttonOf(settingTab, "3. Models", "Download").click();
  await until(() => /✓ Open the Nuru panel/.test(said(settingTab, "4. Ready")));
  assert.deepEqual(settingTab.ollama.pulled, ["qwen3.5:4b", "nomic-embed-text:v1.5"]);
  assert.deepEqual(added(home), { ollama: path.join(home, "Applications", "Ollama.app"), models: ["qwen3.5:4b", "nomic-embed-text:v1.5"] },
    "what Nuru added is recorded, so Remove Nuru can say how to remove them");
});

test("a step at work shows how far it is: a filled bar with bytes for a download, a moving bar otherwise, gone once done", async () => {
  const { settingTab, home, nuru } = tab();
  fs.rmSync(path.join(home, ".nuru", "install.json"));
  const bar = (name) => row(settingTab, name).querySelector("progress");
  const gate = () => { let open; const shut = new Promise((resolve) => { open = resolve; }); return { shut, open }; };
  const [downloaded, unpacked] = [gate(), gate()];
  settingTab.setUp = async ({ step }) => {
    step("Downloading 1 of 3…", 0);
    step("Downloading 1 of 3… 50% · 1.3 of 2.6 MB", 0.5);
    await downloaded.shut;
    step("Unpacking…");
    await unpacked.shut;
    fs.writeFileSync(path.join(home, ".nuru", "install.json"), JSON.stringify({ nuru, uv: path.join(home, "uv") }));
    return { version: "1.0.1" };
  };
  settingTab.display();
  await shown(settingTab);
  assert.equal(bar("1. Nuru"), null, "no bar before anything runs");
  buttonOf(settingTab, "1. Nuru", "Set up Nuru").click();
  await until(() => bar("1. Nuru"));
  assert.match(said(settingTab, "1. Nuru"), /Downloading 1 of 3… 50% · 1\.3 of 2\.6 MB/);
  assert.equal(bar("1. Nuru").value, 0.5);
  downloaded.open();
  await until(() => /Unpacking/.test(said(settingTab, "1. Nuru")));
  assert.equal(bar("1. Nuru").hasAttribute("value"), false, "a step of unknown length moves");
  unpacked.open();
  await until(() => row(settingTab, "Default model"));
  await shown(settingTab);
  assert.equal(bar("1. Nuru"), null);

  const [started, halfway] = [gate(), gate()];
  settingTab.ollama = fakeOllama({ version: "0.40.4", have: ["nomic-embed-text:v1.5"], pull: async (name, onProgress) => {
    onProgress(0, 0);
    await started.shut;
    onProgress(1.5e9, 3e9);
    await halfway.shut;
    settingTab.ollama.have.push(name);
  } });
  settingTab.display();
  await shown(settingTab);
  buttonOf(settingTab, "3. Models", "Download").click();
  await until(() => bar("3. Models"));
  assert.match(said(settingTab, "3. Models"), /Downloading model 1 of 1…$/);
  assert.equal(bar("3. Models").hasAttribute("value"), false, "nothing to count yet");
  started.open();
  await until(() => /50%/.test(said(settingTab, "3. Models")));
  assert.match(said(settingTab, "3. Models"), /Downloading model 1 of 1… 50% · 1\.5 of 3\.0 GB/);
  assert.equal(bar("3. Models").value, 0.5);
  halfway.open();
  await until(() => /✓ Open the Nuru panel/.test(said(settingTab, "4. Ready")));
  assert.equal(bar("3. Models"), null);
});

test("an installed Ollama that isn't running is started; an older one is updated on Windows, and pointed to its updater on macOS", async () => {
  const { settingTab, home } = tab();
  settingTab.ollama = fakeOllama({ version: null, app: "/Applications/Ollama.app" });
  settingTab.display();
  await shown(settingTab);
  assert.match(said(settingTab, "2. Ollama"), /Ollama is installed but not running: click Start Ollama\./);
  buttonOf(settingTab, "2. Ollama", "Start Ollama").click();
  await until(() => /✓ Ollama 0\.40\.4 is running/.test(said(settingTab, "2. Ollama")));
  assert.deepEqual(settingTab.ollama.started, ["/Applications/Ollama.app"]);

  settingTab.ollama = fakeOllama({ version: "0.38.2" });
  settingTab.display();
  await shown(settingTab);
  assert.match(said(settingTab, "2. Ollama"), /Ollama 0\.38\.2 is older than Nuru needs \(0\.40\.4\): update it from Ollama's menu-bar icon/);
  assert.equal(row(settingTab, "2. Ollama").querySelector("a").getAttribute("href"), "https://ollama.com/download");
  assert.equal(buttonOf(settingTab, "2. Ollama", "Install Ollama").disabled, true, "no second copy beside it on macOS");
  assert.match(said(settingTab, "3. Models"), /After step 2\./, "no models into an Ollama too old for them");

  settingTab.system = "win32-x64";
  settingTab.display();
  await shown(settingTab);
  assert.match(said(settingTab, "2. Ollama"), /older than Nuru needs \(0\.40\.4\): click Update Ollama\./);
  buttonOf(settingTab, "2. Ollama", "Update Ollama").click();
  await until(() => /✓ Ollama 0\.40\.4 is running/.test(said(settingTab, "2. Ollama")));
  assert.deepEqual(settingTab.ollama.installed, [["0.40.4", "win32-x64"]]);
  assert.match(said(settingTab, "3. Models"), /✓/);
  assert.equal(added(home).ollama, null, "an Ollama that was there before, started or updated, isn't Nuru's to remove");
});

test("Set up everything runs each step still to do, in order, and stops at one that fails", async () => {
  const { settingTab, home, nuru } = tab();
  fs.rmSync(path.join(home, ".nuru", "install.json"));
  const order = [];
  settingTab.setUp = async () => {
    order.push("nuru");
    fs.writeFileSync(path.join(home, ".nuru", "install.json"), JSON.stringify({ nuru, uv: path.join(home, "uv") }));
    return { version: "1.0.1" };
  };
  settingTab.ollama = fakeOllama({ version: null, have: [] });
  const { install, pull } = settingTab.ollama;
  settingTab.ollama.install = async (...args) => { order.push("ollama"); return install(...args); };
  settingTab.ollama.pull = async (...args) => { order.push(args[0]); return pull(...args); };
  settingTab.display();
  await shown(settingTab);

  assert.match(said(settingTab, "Set up everything"),
    /installs Nuru, installs or updates Ollama \(a separate app, 200 MB\) if needed, and downloads the models \(a few GB\)/,
    "the click is the go-ahead for Ollama too, so it says so first");
  buttonOf(settingTab, "Set up everything", "Set up everything").click();
  await until(() => order.length === 4 && row(settingTab, "Default model"));
  await shown(settingTab);

  assert.deepEqual(order, ["nuru", "ollama", "qwen3.5:4b", "nomic-embed-text:v1.5"]);
  assert.match(said(settingTab, "4. Ready"), /✓ Open the Nuru panel to chat\./);

  const failing = tab();
  failing.settingTab.ollama = fakeOllama({ version: null, have: [], install: async () => { throw new Error("no disk space"); } });
  failing.settingTab.display();
  await shown(failing.settingTab);
  buttonOf(failing.settingTab, "Set up everything", "Set up everything").click();
  await until(() => !buttonOf(failing.settingTab, "Set up everything", "Set up everything").disabled);
  assert.deepEqual(failing.settingTab.ollama.pulled, [], "no models without Ollama");
});

test("with Claude as the default only the embedding model is needed; a dev build without pins points to Ollama's site", async () => {
  const { settingTab, nuru } = tab();
  fs.writeFileSync(path.join(nuru, ".env.nuru"), "NURU_MAIN_MODEL=anthropic:claude-haiku-5-5\n");
  settingTab.release = null;
  settingTab.ollama = fakeOllama({ version: null });
  settingTab.display();
  await shown(settingTab);
  assert.deepEqual(settingTab.ollama.asked, [["nomic-embed-text:v1.5"]]);
  assert.match(said(settingTab, "2. Ollama"), /Ollama isn't running\. Install it from https:\/\/ollama\.com\/download, open it, then click Check\./);
  assert.equal(row(settingTab, "2. Ollama").querySelector("a").getAttribute("href"), "https://ollama.com/download");

  settingTab.ollama.version = "0.30.0"; // no pin to compare with: any running Ollama does
  buttonOf(settingTab, "2. Ollama", "Check").click();
  await until(() => /✓ Ollama 0\.30\.0 is running/.test(said(settingTab, "2. Ollama")));
});

test("a failed install, download or check is said, and can be tried again", async () => {
  const { settingTab } = tab();
  settingTab.ollama = fakeOllama({ version: null, install: async () => { throw new Error("no disk space"); } });
  settingTab.display();
  await shown(settingTab);
  buttonOf(settingTab, "2. Ollama", "Install Ollama").click();
  await until(() => /Install Ollama failed: no disk space/.test(said(settingTab, "2. Ollama")));
  assert.equal(buttonOf(settingTab, "2. Ollama", "Install Ollama").disabled, false);

  settingTab.ollama = fakeOllama({ have: ["nomic-embed-text:v1.5"], pull: async () => { throw new Error("no space left"); } });
  settingTab.display();
  await shown(settingTab);
  buttonOf(settingTab, "3. Models", "Download").click();
  await until(() => /Downloading failed: no space left/.test(said(settingTab, "3. Models")));
  assert.equal(buttonOf(settingTab, "3. Models", "Download").disabled, false);

  settingTab.ollama.check = async () => { throw new Error("Ollama answered 500"); };
  buttonOf(settingTab, "2. Ollama", "Check").click();
  await until(() => /Couldn't ask Ollama about its models: Ollama answered 500/.test(said(settingTab, "2. Ollama")));
});

test("Set up everything starts a stopped Ollama and then updates it if it is older, before any model download", async () => {
  for (const [system, expect] of [["win32-x64", { installed: [["0.40.4", "win32-x64"]], pulled: ["qwen3.5:4b", "nomic-embed-text:v1.5"] }],
    ["darwin-arm64", { installed: [], pulled: [] }]]) {
    const { settingTab } = tab();
    settingTab.system = system;
    const fake = fakeOllama({ version: null, have: [], app: "C:/Ollama/ollama app.exe" });
    fake.start = async (where) => { fake.started.push(where); fake.version = "0.38.2"; }; // what was installed is older
    settingTab.ollama = fake;
    settingTab.display();
    await shown(settingTab);

    buttonOf(settingTab, "Set up everything", "Set up everything").click();
    await until(() => /✓ Open the Nuru panel/.test(said(settingTab, "4. Ready")) || /older than Nuru needs/.test(said(settingTab, "2. Ollama")));
    await shown(settingTab);

    assert.deepEqual(fake.started, ["C:/Ollama/ollama app.exe"]);
    assert.deepEqual(fake.installed, expect.installed);
    assert.deepEqual(fake.pulled, expect.pulled, "models only into an Ollama at the pinned version");
    if (!expect.pulled.length) assert.match(said(settingTab, "2. Ollama"), /0\.38\.2 is older than Nuru needs .* menu-bar icon/);
  }
});

test("Download checks Ollama's version afresh, and refuses one that went older since the tab was drawn", async () => {
  const { settingTab } = tab();
  settingTab.ollama = fakeOllama({ have: ["nomic-embed-text:v1.5"] });
  settingTab.display();
  await shown(settingTab);
  settingTab.ollama.version = "0.38.2"; // replaced by an older Ollama meanwhile
  buttonOf(settingTab, "3. Models", "Download").click();
  await until(() => /After step 2/.test(said(settingTab, "3. Models")));
  assert.deepEqual(settingTab.ollama.pulled, []);
});

// The tab of a plugin Obsidian can remove, with deleting Nuru's files stood in for.
function removable({ stop = async () => {}, problems = [], uninstall = true } = {}) {
  const made = tab();
  const { settingTab } = made;
  const calls = [];
  settingTab.plugin.manifest = { id: "nuru" };
  settingTab.plugin.launcher.stop = async () => { calls.push("stop"); return stop(); };
  settingTab.app.plugins = uninstall ? { uninstallPlugin: async (id) => { calls.push(`uninstall ${id}`); } } : {};
  settingTab.removal = { ...require("../lib/remove"), removeOwn: (options) => {
    calls.push(["own", options.home, options.vault]);
    return { problems: [...problems] };
  } };
  return { ...made, calls };
}

const dialogButton = (text) => [...obsidian.Modal.shown.contentEl.querySelectorAll("button")].find((b) => b.textContent === text);
const openRemove = (settingTab) => {
  settingTab.display();
  buttonOf(settingTab, "Remove Nuru", "Remove Nuru…").click();
  return obsidian.Modal.shown.contentEl.textContent;
};

test("Remove Nuru asks first, saying which files go and how to remove what Nuru added to Ollama; Cancel removes nothing", async () => {
  const { settingTab, home, calls } = removable();
  remember(home, { app: path.join(home, "Applications", "Ollama.app"), model: "qwen3.5:4b" });
  remember(home, { model: "nomic-embed-text:v1.5" });
  const dialog = openRemove(settingTab);
  assert.ok(dialog.includes(`Nuru's install, logs and tokens (${path.join(home, ".nuru")})`));
  assert.match(dialog, /This vault's Nuru chats and search index/);
  assert.match(dialog, /Nuru doesn't remove other programs or their files/);
  assert.match(dialog, /in a terminal: ollama rm qwen3\.5:4b, then ollama rm nomic-embed-text:v1\.5/);
  assert.ok(dialog.includes(`Quit Ollama from its menu-bar icon, then move ${path.join(home, "Applications", "Ollama.app")}, which Nuru installed, to the Bin`));
  assert.match(dialog, /Your notes are never touched/);
  assert.ok(dialogButton("Delete Nuru's files").classList.contains("mod-warning"));
  dialogButton("Cancel").click();
  assert.equal(obsidian.Modal.shown, null);
  assert.deepEqual(calls, []);

  const plain = removable();
  assert.doesNotMatch(openRemove(plain.settingTab), /Nuru doesn't remove other programs/, "no steps when Nuru added nothing to Ollama");
  dialogButton("Cancel").click();
});

test("Keep them removes only the plugin; Delete Nuru's files stops Nuru, deletes them, then removes the plugin, with the steps left", async () => {
  const { settingTab, calls } = removable();
  const before = obsidian.Notice.shown.length;
  openRemove(settingTab);
  dialogButton("Keep them").click();
  await until(() => obsidian.Modal.shown === null);
  assert.deepEqual(calls, ["uninstall nuru"]);
  assert.deepEqual(obsidian.Notice.shown.slice(before), ["The Nuru plugin is removed; Nuru's files are kept."]);

  const all = removable();
  openRemove(all.settingTab);
  dialogButton("Delete Nuru's files").click();
  assert.ok(dialogButton("Keep them").disabled, "one choice at a time");
  await until(() => obsidian.Modal.shown === null);
  assert.deepEqual(all.calls, ["stop", ["own", all.home, all.home], "uninstall nuru"]);
  assert.equal(obsidian.Notice.shown.at(-1), "Nuru and its files are removed.");

  const windows = removable();
  windows.settingTab.system = "win32-x64";
  remember(windows.home, { app: "C:/Programs/Ollama/ollama app.exe", model: "qwen3.5:4b" });
  assert.match(openRemove(windows.settingTab), /Uninstall Ollama, which Nuru installed, in Windows Settings → Apps → Installed apps/);
  dialogButton("Delete Nuru's files").click();
  await until(() => obsidian.Modal.shown === null);
  assert.equal(obsidian.Notice.shown.at(-1), "Nuru and its files are removed.\nStill to do, to remove what Nuru added to Ollama:\n" +
    "Delete the models Nuru downloaded, in a terminal: ollama rm qwen3.5:4b\nUninstall Ollama, which Nuru installed, in Windows Settings → Apps → Installed apps");
});

test("what can't be removed is said: a Nuru that won't stop stops it all; a folder left, or a plugin Obsidian won't remove, is listed", async () => {
  const stuck = removable({ stop: async () => { throw new LaunchProblem("busy"); } });
  openRemove(stuck.settingTab);
  dialogButton("Delete Nuru's files").click();
  await until(() => /^Nothing was removed: Nuru is busy/.test(obsidian.Notice.shown.at(-1)));
  assert.deepEqual(stuck.calls, ["stop"]);

  const partly = removable({ problems: ["This vault's .nuru is still there: EBUSY"], uninstall: false });
  openRemove(partly.settingTab);
  dialogButton("Delete Nuru's files").click();
  await until(() => obsidian.Modal.shown === null);
  assert.equal(obsidian.Notice.shown.at(-1), "Nuru was only partly removed:\nThis vault's .nuru is still there: EBUSY\n" +
    "The plugin is still there: remove it in Settings → Community plugins");
});
