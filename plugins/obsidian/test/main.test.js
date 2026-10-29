"use strict";
// The plugin's entry point (main.js) against the Obsidian stand-in: what it registers, and opening the panel.
require("./helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const obsidian = require("obsidian");
const NuruPlugin = require("../main");
const { DEFAULT_PORT, ConnectionProblem } = require("../lib/connection");
const { LANTERN } = require("../lib/icon");
const { NuruSettingTab } = require("../lib/settings");
const { ChatView, VIEW_TYPE } = require("../lib/view");

function app(vault) {
  const leaves = [], revealed = [];
  return {
    revealed,
    leaves,
    vault: { adapter: { getBasePath: () => vault } },
    workspace: {
      getLeavesOfType: (type) => leaves.filter((leaf) => leaf.type === type),
      getRightLeaf: () => {
        const leaf = { type: null, async setViewState(state) { Object.assign(this, state); } };
        leaves.push(leaf);
        return leaf;
      },
      revealLeaf: (leaf) => revealed.push(leaf),
    },
  };
}

async function loaded(t, saved) {
  const vault = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-main-"));
  t.after(() => fs.rmSync(vault, { recursive: true, force: true }));
  const plugin = new NuruPlugin(app(vault), { version: "1.0.4" });
  plugin._data = saved;
  await plugin.onload();
  return { plugin, vault };
}

test("loading registers the icon, the panel, the ribbon, the command and the settings, over saved settings", async (t) => {
  const { plugin, vault } = await loaded(t, { port: 27199, conversationId: "conv0000trip" });
  assert.deepEqual(plugin.settings, { port: 27199, conversationId: "conv0000trip", autoStart: true });
  assert.ok(obsidian.icons[LANTERN].includes("<"));
  assert.deepEqual(plugin.app.ribbon.map(({ icon, title }) => [icon, title]), [[LANTERN, "Open Nuru"]]);
  assert.deepEqual(plugin.app.commands.map(({ id, name }) => [id, name]), [["open", "Open chat"]]);
  assert.ok(plugin.app.settingTabs[0] instanceof NuruSettingTab);
  assert.equal(plugin.vaultPath(), vault);
  assert.ok(plugin.app.views[VIEW_TYPE]({ app: plugin.app }) instanceof ChatView);
});

test("with nothing saved the defaults are used", async (t) => {
  const { plugin } = await loaded(t, null);
  assert.deepEqual(plugin.settings, { port: DEFAULT_PORT, conversationId: null, autoStart: true });
});

test("opening makes the panel once and then brings the same one forward", async (t) => {
  const { plugin } = await loaded(t, null);
  await plugin.app.ribbon[0].callback();
  await plugin.app.commands[0].callback();
  assert.equal(plugin.app.leaves.length, 1);
  assert.deepEqual([plugin.app.leaves[0].type, plugin.app.leaves[0].active], [VIEW_TYPE, true]);
  assert.deepEqual(plugin.app.revealed, [plugin.app.leaves[0], plugin.app.leaves[0]]);
});

test("remember saves the changed settings, and views lists only Nuru's chat panels", async (t) => {
  const { plugin } = await loaded(t, null);
  await plugin.remember({ conversationId: "conv0000next" });
  assert.equal((await plugin.loadData()).conversationId, "conv0000next");
  const chat = Object.create(ChatView.prototype);
  plugin.app.leaves.push({ type: VIEW_TYPE, view: chat }, { type: VIEW_TYPE, view: {} });
  assert.deepEqual(plugin.views(), [chat]);
});

test("connecting reads this computer's token for the plugin's port and vault", async (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-home-"));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  t.mock.method(os, "homedir", () => home); // no token here: refused before any request
  const { plugin } = await loaded(t, null);
  await assert.rejects(plugin.connect(), (error) => error instanceof ConnectionProblem && error.kind === "no-token");
});

test("unloading releases the service this plugin started", async (t) => {
  const { plugin } = await loaded(t, null);
  const release = t.mock.method(plugin.launcher, "release");
  plugin.onunload();
  assert.equal(release.mock.callCount(), 1);
});
