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

function tab({ connect } = {}) {
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
    launcher: { home, restart: async () => { restarts.push(1); return {}; } },
    views: () => [{ reconnect: async () => reconnects.push(1) }],
    connect: connect || (async () => ({ status: { models: { default: "qwen3.5:4b", choices: [
      { id: "qwen3.5:4b", label: "qwen3.5:4b", cloud: false }, { id: "granite4.2:3b", label: "granite4.2:3b", cloud: false },
      { id: "anthropic:claude-haiku-5-5", label: "Claude Haiku 5.5", cloud: true }] } } })),
  };
  const settingTab = new NuruSettingTab({}, plugin);
  return { settingTab, nuru, restarts, reconnects };
}

const row = (settingTab, name) => [...settingTab.containerEl.children].find((el) => el.firstChild?.textContent === name);
const change = (el, value) => { el.value = value; el.dispatchEvent(new window.Event(el.tagName === "SELECT" ? "change" : "input")); };

test("the Claude key shows only its fingerprint, and Anthropic is asked whether it still works", async (t) => {
  const asked = [];
  obsidian.requestUrl.handler = async (request) => { asked.push(request.headers["x-api-key"]); return { status: 401 }; };
  t.after(() => { obsidian.requestUrl.handler = async () => ({ status: 200 }); });
  const { settingTab, nuru } = tab();
  settingTab.display();
  await settingTab.checked;
  const key = row(settingTab, "Claude API key");
  assert.match(key.textContent, /fingerprint [0-9a-f]{8}\)\. Anthropic rejects it: paste a new one/);
  assert.ok(!settingTab.containerEl.innerHTML.includes(KEY), "the key itself is never on the page");
  assert.deepEqual(asked, [KEY]);

  obsidian.requestUrl.handler = async (request) => { asked.push(request.headers["x-api-key"]); return { status: 200 }; };
  const input = key.querySelector("input");
  assert.equal(input.type, "password");
  change(input, " sk-ant-api03-new ");
  key.querySelector("button").click();
  await until(() => /accepts it/.test(key.textContent));
  assert.equal(readEnv(path.join(nuru, ".env.deep-think")).CLAUDE_API_KEY, "sk-ant-api03-new");
  assert.equal(input.value, "", "the field is cleared once it's saved");
  assert.equal(asked.at(-1), "sk-ant-api03-new");
  assert.match(row(settingTab, "Apply changes").textContent, /restart Nuru to use it/);
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
