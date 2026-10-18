// Nuru for Obsidian (WP01 M3): chat with the local nuru-serve from a sidebar. Desktop only: the token
// is read from a file outside the vault (~/.nuru/service/clients/obsidian.token), never stored in
// plugin data, which syncs. Only the port, the last conversation and whether to start Nuru are saved
// here. Opening the panel starts nuru-serve for this vault if it isn't running (lib/launcher.js).
"use strict";

const { Plugin, addIcon } = require("obsidian");

const { DEFAULT_PORT, connect } = require("./lib/connection");
const { LANTERN, LANTERN_SVG } = require("./lib/icon");
const { Launcher } = require("./lib/launcher");
const { NuruSettingTab } = require("./lib/settings");
const { ChatView, VIEW_TYPE } = require("./lib/view");

module.exports = class NuruPlugin extends Plugin {
  async onload() {
    this.settings = { port: DEFAULT_PORT, conversationId: null, autoStart: true, ...((await this.loadData()) || {}) };
    this.launcher = new Launcher({ vaultPath: this.vaultPath(), port: this.settings.port, connect: () => this.connect() });
    addIcon(LANTERN, LANTERN_SVG);
    this.registerView(VIEW_TYPE, (leaf) => new ChatView(leaf, this));
    this.addRibbonIcon(LANTERN, "Open Nuru", () => this.open());
    this.addCommand({ id: "open", name: "Open chat", callback: () => this.open() });
    this.addSettingTab(new NuruSettingTab(this.app, this));
  }

  onunload() {
    this.launcher.release(); // a service this plugin started stops with it
  }

  async open() {
    let leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false);
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    this.app.workspace.revealLeaf(leaf);
  }

  // A client for nuru-serve, once it shows it serves this vault (lib/connection.js).
  connect() {
    return connect({ port: this.settings.port, vaultPath: this.vaultPath() });
  }

  vaultPath() {
    return this.app.vault.adapter.getBasePath();
  }

  async remember(changes) {
    Object.assign(this.settings, changes);
    await this.saveData(this.settings);
  }

  views() {
    return this.app.workspace.getLeavesOfType(VIEW_TYPE).map((leaf) => leaf.view).filter((view) => view instanceof ChatView);
  }
};
