// Settings: the service's port, and whether the plugin can reach it for this vault.
"use strict";

const { PluginSettingTab, Setting } = require("obsidian");

const { tokenPath, vaultId, ConnectionProblem } = require("./connection");
const { logPath } = require("./launcher");

class NuruSettingTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    const { containerEl } = this;
    containerEl.empty();
    new Setting(containerEl)
      .setName("Port")
      .setDesc("Where nuru-serve listens on this computer (127.0.0.1). The default is 27125.")
      .addText((text) => text.setValue(String(this.plugin.settings.port)).onChange(async (value) => {
        const port = Number(value);
        if (Number.isInteger(port) && port > 0 && port < 65536) {
          await this.plugin.remember({ port });
          this.plugin.launcher.port = port;
        }
      }));
    new Setting(containerEl)
      .setName("Start Nuru when the panel opens")
      .setDesc(`If it isn't running, opening the panel starts it for this vault, and it stops when Obsidian quits. ` +
        `Its output goes to ${logPath()}.`)
      .addToggle((toggle) => toggle.setValue(this.plugin.settings.autoStart !== false)
        .onChange((autoStart) => this.plugin.remember({ autoStart })));
    new Setting(containerEl)
      .setName("Token")
      .setDesc(`Read from ${tokenPath()}, outside the vault, so it never syncs. Starting Nuru from here makes ` +
        "one if there's none; or run `uv run nuru-serve token add obsidian`.");
    new Setting(containerEl).setName("This vault").setDesc(`ID ${vaultId(this.plugin.vaultPath())}: Nuru must serve this one.`);
    const result = containerEl.createDiv({ cls: "nuru-settings-result" });
    new Setting(containerEl).setName("Connection").addButton((button) => button.setButtonText("Check and reconnect")
      .onClick(async () => {
        result.setText("Checking…");
        try {
          const { status } = await this.plugin.connect();
          result.setText(`Connected to Nuru ${status.version}, serving "${status.vault.name}". Chat: ${status.ready.chat}.`);
          for (const view of this.plugin.views()) await view.reconnect();
        } catch (error) {
          result.setText(error instanceof ConnectionProblem ? error.message : `Couldn't connect: ${error.message}`);
        }
      }));
  }
}

module.exports = { NuruSettingTab };
