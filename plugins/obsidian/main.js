// Nuru for Obsidian (WP01 M3): chat with the local nuru-serve from a sidebar. Desktop only: the token
// is read from a file outside the vault (~/.nuru/service/clients/obsidian.token), never stored in
// plugin data, which syncs. Only the port and the last conversation are saved here.
"use strict";

const { Plugin } = require("obsidian");

const { DEFAULT_PORT, connect } = require("./lib/connection");
const { NuruSettingTab } = require("./lib/settings");
const { ChatView, VIEW_TYPE } = require("./lib/view");

module.exports = class NuruPlugin extends Plugin {
  async onload() {
    this.settings = { port: DEFAULT_PORT, conversationId: null, ...((await this.loadData()) || {}) };
    this.registerView(VIEW_TYPE, (leaf) => new ChatView(leaf, this));
    this.addRibbonIcon("message-circle", "Open Nuru", () => this.open());
    this.addCommand({ id: "open", name: "Open chat", callback: () => this.open() });
    this.addSettingTab(new NuruSettingTab(this.app, this));
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
