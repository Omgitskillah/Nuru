// Settings: the service's port, whether the plugin can reach it for this vault, and Nuru's own settings,
// which live in the env files in the Nuru folder (lib/envfiles.js), never in plugin data.
"use strict";

const obsidian = require("obsidian");

const { NuruSettings, fingerprint } = require("./envfiles");
const { tokenPath, vaultUuid, ConnectionProblem } = require("./connection");
const { LaunchProblem, logPath, readInstall } = require("./launcher");
const { RELEASE, SetupProblem, restoreInstall, setUp } = require("./setup");
const ollama = require("./ollama");

const { PluginSettingTab, Setting } = obsidian;

const KEEP_ALIVE = { "5m": "5 minutes", "30m": "30 minutes", "1h": "1 hour", "-1": "Until Ollama stops" };
const DEFAULT_KEEP_ALIVE = "30m"; // Nuru's own when NURU_KEEP_ALIVE isn't set (core/providers/ollama.py)

// "valid", "rejected" or "unreachable": Anthropic's model list, which costs nothing.
async function checkClaudeKey(key) {
  try {
    const response = await obsidian.requestUrl({ url: "https://api.anthropic.com/v1/models", throw: false,
      headers: { "x-api-key": key, "anthropic-version": "2023-06-01" } });
    if (response.status === 200) return "valid";
    return response.status === 401 || response.status === 403 ? "rejected" : "unreachable";
  } catch {
    return "unreachable";
  }
}

class NuruSettingTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
    this.setUp = setUp;
    this.ollama = ollama;
    this.setupNews = "";
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
    const own = vaultUuid(this.plugin.vaultPath());
    new Setting(containerEl).setName("This vault").setDesc(own ? `ID ${own} (in .nuru/vault-id): Nuru must serve this one.`
      : "No ID yet: Nuru gives the vault one (.nuru/vault-id) the first time it serves it.");
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
    this.displayNuru(containerEl);
  }

  // "Set up Nuru", or repair or update it: the release this plugin came with (lib/setup.js). Nuru in
  // use is restarted on the new install; this tab is then shown again, saying how it went.
  displaySetup(containerEl, installed) {
    const row = new Setting(containerEl).setName(installed ? "Repair or update Nuru" : "Set up Nuru");
    const say = (text) => row.setDesc(text);
    say(this.setupNews || (installed
      ? `Installs Nuru ${RELEASE?.version ?? ""} again beside the one in use, and switches to it once it works.`
      : "Downloads Nuru and what it needs (a few hundred MB) into a .nuru folder in your home folder. Your notes aren't touched."));
    this.setupNews = "";
    row.addButton((button) => button.setButtonText(installed ? "Repair" : "Set up Nuru").onClick(async () => {
      button.setDisabled(true);
      const { launcher } = this.plugin;
      const restart = async () => {
        await launcher.restart();
        for (const view of this.plugin.views()) await view.reconnect();
      };
      try {
        const { version, previous } = await this.setUp({ home: launcher.home, step: say });
        this.setupNews = `Nuru ${version} is set up.`;
        if (launcher.running()) {
          say("Restarting Nuru on the new install…");
          try {
            await restart();
          } catch (error) {
            // The new install doesn't start: go back to the one before it, which setUp kept.
            this.setupNews = `Nuru ${version} was set up, but it didn't start: ${error.message}`;
            if (previous) {
              try {
                restoreInstall(launcher.home, previous);
                await restart();
                this.setupNews += " Nuru is back on the install it had before.";
              } catch (back) {
                this.setupNews += ` Going back to the install it had before failed too: ${back.message}`;
              }
            }
          }
        }
      } catch (error) {
        this.setupNews = error instanceof SetupProblem ? error.message : `Setting Nuru up failed: ${error.message}`;
      }
      this.display();
    }));
  }

  // Ollama and the models Nuru needs from it: the chat model (none when Claude is the default) and the
  // notes' embedding model. Missing models download through Ollama; Ollama itself, from its installer.
  displayOllama(containerEl, chatModel) {
    const needed = [chatModel, this.ollama.EMBEDDING_MODEL].filter(Boolean);
    const row = new Setting(containerEl).setName("Ollama");
    const say = (text) => row.setDesc(text);
    let missing = [];
    const check = async () => {
      say("Checking…");
      try {
        const found = await this.ollama.check(needed);
        missing = found.missing;
        download.setDisabled(!found.running || !missing.length);
        if (!found.running) {
          say(`Ollama isn't running. Install it from ${this.ollama.DOWNLOAD}, open it, then click Check.`);
        } else {
          say(missing.length ? `Ollama ${found.version} is running, but doesn't have ${missing.join(" and ")} yet: click Download (a few GB).`
            : `Ollama ${found.version} has the models Nuru needs: ${needed.join(" and ")}.`);
        }
      } catch (error) {
        say(`Couldn't ask Ollama about its models: ${error.message}`);
      }
    };
    let download;
    row.addButton((button) => button.setButtonText("Check").onClick(check));
    row.addButton((button) => {
      download = button.setButtonText("Download").setDisabled(true).onClick(async () => {
        download.setDisabled(true);
        try {
          for (const name of missing) await this.ollama.pull(name, say);
          await check();
        } catch (error) {
          say(`Downloading failed: ${error.message}`);
          download.setDisabled(false);
        }
      });
    });
    this.ollamaChecked = check();
  }

  // Nuru's settings, read from and written to its env files; Nuru reads them when it starts.
  displayNuru(containerEl) {
    new Setting(containerEl).setName("Nuru").setHeading();
    let settings;
    try {
      settings = new NuruSettings(readInstall(this.plugin.launcher.home).nuru);
    } catch {
      this.displaySetup(containerEl, false);
      this.displayOllama(containerEl, ollama.CHAT_MODEL);
      return;
    }
    this.displaySetup(containerEl, true);
    const chosen = settings.defaultModel() || ollama.CHAT_MODEL;
    this.displayOllama(containerEl, chosen.startsWith("anthropic:") ? null : chosen);
    const overridden = (key) => {
      const name = settings.overridden(key);
      return name ? ` Obsidian's environment sets ${name}, which Nuru uses instead.` : "";
    };
    const apply = new Setting(containerEl).setName("Apply changes");
    const applied = (text) => apply.setDesc(text);
    applied(`Saved in .env.nuru and .env.deep-think in ${settings.nuru}, where Nuru reads them when it starts: ` +
      "editing those files works too.");
    const changed = () => applied("Changed: restart Nuru to use it.");
    apply.addButton((button) => button.setButtonText("Restart Nuru").onClick(async () => {
      applied("Restarting…");
      try {
        await this.plugin.launcher.restart();
        for (const view of this.plugin.views()) await view.reconnect();
        applied("Restarted with these settings.");
      } catch (error) {
        applied(error instanceof LaunchProblem || error instanceof ConnectionProblem ? error.message
          : `Couldn't restart Nuru: ${error.message}`);
      }
    }));

    const current = settings.defaultModel();
    const model = new Setting(containerEl).setName("Default model")
      .setDesc("New conversations start on it; each conversation can still pick another." +
        overridden(current?.startsWith("anthropic:") ? "NURU_MAIN_MODEL" : "NURU_BASE_MODEL"));
    model.addDropdown((dropdown) => {
      if (current) dropdown.addOption(current, current);
      dropdown.setValue(current || "");
      dropdown.onChange((id) => {
        settings.setDefaultModel(id);
        changed();
      });
      // The models a conversation can pick, as Nuru lists them: what its profiles support, Claude with a key.
      this.plugin.connect().then(({ status }) => {
        for (const choice of status.models?.choices || []) {
          if (choice.id !== current) dropdown.addOption(choice.id, choice.label + (choice.cloud ? " (cloud)" : ""));
        }
        dropdown.setValue(current || "");
      }).catch(() => model.setDesc("Start Nuru to choose from the models it can run."));
    });

    const keepAlive = settings.get("NURU_KEEP_ALIVE") || DEFAULT_KEEP_ALIVE;
    new Setting(containerEl).setName("Keep the local model loaded")
      .setDesc("After each reply, so the next starts quickly; its memory stays in use as long." + overridden("NURU_KEEP_ALIVE"))
      .addDropdown((dropdown) => {
        for (const [value, label] of Object.entries(KEEP_ALIVE)) dropdown.addOption(value, label);
        if (!(keepAlive in KEEP_ALIVE)) dropdown.addOption(keepAlive, keepAlive);
        dropdown.setValue(keepAlive).onChange((value) => {
          settings.set("NURU_KEEP_ALIVE", value);
          changed();
        });
      });

    const key = new Setting(containerEl).setName("Claude API key");
    const describe = (state = "") => {
      const saved = settings.get("CLAUDE_API_KEY");
      key.setDesc((saved ? `Saved in .env.deep-think (fingerprint ${fingerprint(saved)}).${state}`
        : "None saved: Claude Haiku and Sonnet aren't offered.") + overridden("CLAUDE_API_KEY"));
    };
    const check = async () => {
      const saved = settings.get("CLAUDE_API_KEY");
      if (!saved) return describe();
      describe(" Checking…");
      const result = await checkClaudeKey(saved);
      if (settings.get("CLAUDE_API_KEY") !== saved) return; // another key was saved meanwhile: this says nothing about it
      describe({ valid: " Anthropic accepts it.", rejected: " Anthropic rejects it: paste a new one.",
        unreachable: " Couldn't reach Anthropic to check it." }[result]);
    };
    let typed = "";
    key.addText((text) => {
      text.inputEl.type = "password";
      text.setPlaceholder("Paste a new key").onChange((value) => { typed = value.trim(); });
      key.addButton((button) => button.setButtonText("Save").onClick(async () => {
        if (!typed) return;
        settings.set("CLAUDE_API_KEY", typed);
        typed = "";
        text.setValue("");
        changed();
        describe();
      }));
    });
    // Only on request: the saved key goes to Anthropic when Check is clicked, never by itself.
    key.addButton((button) => button.setButtonText("Check").onClick(check));
    describe();
  }
}

module.exports = { NuruSettingTab, checkClaudeKey };
