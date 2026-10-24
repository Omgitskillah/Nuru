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
  return { settingTab, nuru, home, restarts, reconnects };
}

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
const { LaunchProblem } = require("../lib/launcher");

test("with Nuru not set up, Set up Nuru installs it, and the tab then shows Nuru's settings", async () => {
  const { settingTab, home, nuru, restarts } = tab();
  fs.rmSync(path.join(home, ".nuru", "install.json"));
  const calls = [];
  settingTab.setUp = async (options) => {
    calls.push(options.home);
    options.step("Downloading Nuru…");
    fs.writeFileSync(path.join(home, ".nuru", "install.json"), JSON.stringify({ nuru, uv: path.join(home, "uv") }));
    return { version: "0.1.0", nuru };
  };
  settingTab.display();
  assert.equal(row(settingTab, "Default model"), undefined);
  const setup = row(settingTab, "Set up Nuru");
  assert.match(setup.textContent, /Downloads Nuru and what it needs .* Your notes aren't touched/);

  setup.querySelector("button").click();
  await until(() => row(settingTab, "Repair or update Nuru"));

  assert.deepEqual(calls, [home]);
  assert.match(row(settingTab, "Repair or update Nuru").textContent, /Nuru 0\.1\.0 is set up\./);
  assert.ok(row(settingTab, "Default model"), "Nuru's own settings are shown now");
  assert.deepEqual(restarts, [], "nothing was running, so nothing restarts");
  settingTab.display();
  assert.doesNotMatch(row(settingTab, "Repair or update Nuru").textContent, /is set up/, "the news is shown once");
});

test("a repair while Nuru runs restarts it on the new install and reconnects the chat", async () => {
  const { settingTab, restarts, reconnects } = tab({ running: true });
  settingTab.setUp = async () => ({ version: "0.1.0" });
  settingTab.display();

  row(settingTab, "Repair or update Nuru").querySelector("button").click();
  await until(() => restarts.length && /is set up/.test(row(settingTab, "Repair or update Nuru").textContent));

  assert.deepEqual([restarts.length, reconnects.length], [1, 1]);
});

test("what went wrong in a setup, or in the restart after it, is said in the tab", async () => {
  const cases = [
    [async () => { throw new SetupProblem("Couldn't download uv: the server answered 404"); }, false,
      "Couldn't download uv: the server answered 404"],
    [async () => { throw new Error("EACCES"); }, false, "Setting Nuru up failed: EACCES"],
    [async () => ({ version: "0.1.0" }), true, "Nuru 0.1.0 is set up. Restarting it failed: Nuru stopped while starting."],
  ];
  for (const [setUp, running, said] of cases) {
    const { settingTab } = tab({ running });
    settingTab.plugin.launcher.restart = async () => { throw new LaunchProblem("exited"); };
    settingTab.setUp = setUp;
    settingTab.display();
    row(settingTab, "Repair or update Nuru").querySelector("button").click();
    await until(() => row(settingTab, "Repair or update Nuru").textContent.includes(said));
  }
});
