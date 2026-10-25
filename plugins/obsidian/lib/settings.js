// Settings: the service's port, whether the plugin can reach it for this vault, and Nuru's own settings,
// which live in the env files in the Nuru folder (lib/envfiles.js), never in plugin data.
"use strict";

const path = require("node:path");

const obsidian = require("obsidian");

const { NuruSettings, fingerprint } = require("./envfiles");
const { tokenPath, vaultUuid, ConnectionProblem } = require("./connection");
const { LaunchProblem, logPath, readInstall } = require("./launcher");
const { RELEASE, SetupProblem, progress: downloading, setUp, thisSystem } = require("./setup");
const ollama = require("./ollama");
const removal = require("./remove");

const { Modal, Notice, PluginSettingTab, Setting } = obsidian;

const KEEP_ALIVE = { "5m": "5 minutes", "30m": "30 minutes", "1h": "1 hour", "-1": "Until Ollama stops" };
const DEFAULT_KEEP_ALIVE = "30m"; // Nuru's own when NURU_KEEP_ALIVE isn't set (core/providers/ollama.py)

// Asks how to remove Nuru: keep what it added for later, or delete all of it (`parts` lists that), then
// runs `remove(everything, say)`, showing what it's doing until it's done.
class RemoveModal extends Modal {
  constructor(app, parts, remove) {
    super(app);
    Object.assign(this, { parts, remove });
  }

  onOpen() {
    const { contentEl } = this;
    this.setTitle("Remove Nuru");
    contentEl.createEl("p", { text: "This removes the Nuru plugin. Should it delete what Nuru added to this computer too?" });
    const list = contentEl.createEl("ul");
    for (const part of this.parts) list.createEl("li", { text: part });
    contentEl.createEl("p", { text: "Your notes are never touched. Ollama and models you had before Nuru stay." });
    const status = contentEl.createEl("p", { cls: "nuru-remove-status" });
    const buttons = new Setting(contentEl);
    const choose = (everything) => async () => {
      for (const button of contentEl.querySelectorAll("button")) button.disabled = true;
      await this.remove(everything, (text) => status.setText(text));
      this.close();
    };
    buttons.addButton((b) => b.setButtonText("Cancel").onClick(() => this.close()));
    buttons.addButton((b) => b.setButtonText("Keep them").onClick(choose(false)));
    buttons.addButton((b) => b.setButtonText("Delete everything").setWarning().onClick(choose(true)));
  }

  onClose() {
    this.contentEl.empty();
  }
}

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
    this.removal = removal;
    this.release = RELEASE;
    this.system = thisSystem();
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
    this.displayRemove(containerEl);
  }

  // Removing the plugin, and on request what Nuru added: Obsidian doesn't tell a plugin it's being
  // removed (only that it's unloaded, as when Obsidian quits), so it's asked here, and the plugin removes itself.
  displayRemove(containerEl) {
    new Setting(containerEl).setName("Remove").setHeading();
    const { home } = this.plugin.launcher;
    const vault = this.plugin.vaultPath();
    const { ollama: app, models } = this.removal.added(home);
    const parts = [`Nuru's install, logs and tokens (${path.join(home, ".nuru")}), and uv's download cache`,
      ...(models.length ? [`The models Nuru downloaded into Ollama: ${models.join(" and ")}`] : []),
      ...(app ? [`Ollama, which Nuru installed (${app})`] : []),
      `This vault's Nuru chats and search index (its .nuru folder)`];
    new Setting(containerEl).setName("Remove Nuru")
      .setDesc("Removes this plugin. It asks first whether to delete what Nuru added to this computer too, or keep it for later.")
      .addButton((button) => button.setButtonText("Remove Nuru…").setWarning()
        .onClick(() => new RemoveModal(this.app, parts, (everything, say) => this.remove(everything, say, vault)).open()));
  }

  // Deletes everything Nuru added, if asked, once Nuru has stopped; then removes the plugin. A Notice says
  // how it went, since the settings go with the plugin.
  async remove(everything, say, vault) {
    const { launcher, manifest } = this.plugin;
    let problems = [];
    if (everything) {
      say("Stopping Nuru…");
      try {
        await launcher.stop();
      } catch (error) {
        new Notice(`Nothing was removed: ${error.message}`, 0);
        return;
      }
      problems = await this.removal.removeEverything({ home: launcher.home, vault, say });
    }
    say("Removing the plugin…");
    try {
      await this.app.plugins.uninstallPlugin(manifest.id); // Obsidian's own, not in its published API
    } catch {
      problems.push("The plugin is still there: remove it in Settings → Community plugins");
    }
    const done = everything ? "Nuru and what it added are removed." : "The Nuru plugin is removed; what Nuru added is kept.";
    new Notice(problems.length ? `${done} Except:\n${problems.join("\n")}` : done, problems.length ? 0 : undefined);
  }

  // Setting Nuru up as numbered steps, each enabled once the one before is done: 1 Nuru, the release
  // this plugin came with (lib/setup.js); 2 Ollama, at the version the release pins or newer, installed,
  // updated or started from here (lib/ollama.js); 3 the models Nuru needs from it (the chat model, none
  // when Claude is the default, and the notes' embedding model); 4 ready. "Set up everything" runs the
  // steps still to do, in order. When it's done, this tab is shown again, saying how it went. A step at
  // work shows a bar under its text: filled as far as a download is, or moving when how long isn't known.
  displaySteps(containerEl, installed, chatModel) {
    new Setting(containerEl).setName("Set up").setHeading();
    const { launcher } = this.plugin;
    const pin = this.release?.ollama;
    const windows = this.system.startsWith("win32");
    const needed = [chatModel, this.ollama.EMBEDDING_MODEL].filter(Boolean);
    const state = { running: false, version: null, app: null, missing: needed, ok: false };
    const news = { nuru: this.setupNews, ollama: "", models: "" }; // shown once, by these rows
    this.setupNews = "";
    const rows = {};
    const buttons = {};
    const bars = {};
    const progress = (key) => (text, share) => {
      rows[key].setDesc(text);
      bars[key] ??= rows[key].infoEl.createEl("progress", { cls: "nuru-progress" });
      if (share === undefined) bars[key].removeAttribute("value");
      else bars[key].value = share;
    };
    const row = (key, name) => { rows[key] = new Setting(containerEl).setName(name); };
    const button = (key, text, action, on = key) => rows[on].addButton((b) => {
      buttons[key] = b.setButtonText(text).onClick(async () => {
        for (const other of Object.values(buttons)) other.setDisabled(true);
        await action();
        await refresh();
      });
    });
    const link = (before, after) => createFragment((text) => {
      text.appendText(before);
      text.createEl("a", { text: this.ollama.DOWNLOAD, attr: { href: this.ollama.DOWNLOAD } });
      text.appendText(after);
    });

    const setUpNuru = async () => {
      const restart = async () => {
        await launcher.restart();
        for (const view of this.plugin.views()) await view.reconnect();
      };
      try {
        const result = await this.setUp({ home: launcher.home, step: progress("nuru"), start: launcher.running() ? restart : null });
        this.setupNews = `Nuru ${result.version} is set up.`;
        if (result.startError) {
          this.setupNews = `Nuru ${result.version} was set up, but it didn't start: ${result.startError.message}`;
          if (result.rolledBack) this.setupNews += " Nuru is back on the install it had before.";
          if (result.rollbackError) this.setupNews += ` Going back to the install it had before failed too: ${result.rollbackError.message}`;
        }
        return !result.startError;
      } catch (error) {
        this.setupNews = error instanceof SetupProblem ? error.message : `Setting Nuru up failed: ${error.message}`;
        return false;
      }
    };
    // What step 2's button does now: install, update or start Ollama, or nothing.
    const ollamaAction = () => {
      if (!pin || (state.running && (state.ok || !windows))) return null;
      if (state.running) return "Update Ollama";
      return state.app ? "Start Ollama" : "Install Ollama";
    };
    const fixOllama = async () => {
      const say = progress("ollama");
      const installing = ollamaAction() === "Install Ollama"; // none was there, so Nuru adds it
      try {
        if (ollamaAction() === "Start Ollama") {
          say("Starting Ollama…");
          await this.ollama.start(state.app);
        } else await this.ollama.install(pin, this.system, { home: launcher.home, step: say });
        if (installing) this.removal.remember(launcher.home, { app: this.ollama.findApp({ home: launcher.home }) });
        return true;
      } catch (error) {
        news.ollama = `${ollamaAction()} failed: ${error.message}`;
        return false;
      }
    };
    // Models go only into an Ollama confirmed, just now, to be at the pinned version or newer.
    const downloadModels = async () => {
      try {
        const found = await this.ollama.check(needed);
        if (!found.running || (pin && !this.ollama.atLeast(found.version, pin.version))) {
          news.models = "Ollama isn't at the version Nuru needs yet: finish step 2 first.";
          return false;
        }
        const say = progress("models");
        for (const [i, name] of found.missing.entries()) {
          const text = `Downloading model ${i + 1} of ${found.missing.length}…`;
          say(text, 0);
          await this.ollama.pull(name, downloading(say, text));
          this.removal.remember(launcher.home, { model: name });
        }
        return true;
      } catch (error) {
        news.models = `Downloading failed: ${error.message}`;
        return false;
      }
    };

    const refresh = async () => {
      try {
        const found = await this.ollama.check(needed);
        Object.assign(state, found, { app: this.ollama.findApp({ home: launcher.home }) });
        state.ok = found.running && (!pin || this.ollama.atLeast(found.version, pin.version));
      } catch (error) {
        Object.assign(state, { running: false, ok: false });
        news.ollama ||= `Couldn't ask Ollama about its models: ${error.message}`;
      }
      for (const key of Object.keys(bars)) {
        bars[key].remove();
        delete bars[key];
      }
      const action = ollamaAction();
      rows.nuru.setDesc(news.nuru || (installed
        ? `✓ Nuru is set up. Repair installs Nuru ${this.release?.version ?? ""} again beside it, and switches once the new one works.`
        : "Downloads Nuru and what it needs into a .nuru folder in your home folder. Your notes aren't touched."));
      if (!installed) rows.ollama.setDesc("After step 1.");
      else if (news.ollama) rows.ollama.setDesc(news.ollama);
      else if (state.ok) rows.ollama.setDesc(`✓ Ollama ${state.version} is running.`);
      else if (state.running && action) rows.ollama.setDesc(`Ollama ${state.version} is older than Nuru needs (${pin.version}): click Update Ollama.`);
      else if (state.running && pin) rows.ollama.setDesc(link(`Ollama ${state.version} is older than Nuru needs (${pin.version}): update it from Ollama's menu-bar icon, or from `, ", then click Check."));
      else if (action === "Start Ollama") rows.ollama.setDesc("Ollama is installed but not running: click Start Ollama.");
      else if (action) rows.ollama.setDesc(`Ollama runs Nuru's models. Install Ollama downloads and installs Ollama ${pin.version} for you (${windows ? "1.5 GB" : "200 MB"}).`);
      else if (state.running) rows.ollama.setDesc(`✓ Ollama ${state.version} is running.`);
      else rows.ollama.setDesc(link("Ollama isn't running. Install it from ", ", open it, then click Check."));
      if (!state.ok) rows.models.setDesc("After step 2.");
      else if (news.models) rows.models.setDesc(news.models);
      else rows.models.setDesc(state.missing.length ? `Ollama doesn't have ${state.missing.join(" and ")} yet: click Download (a few GB).`
        : `✓ Ollama has the models Nuru needs: ${needed.join(" and ")}.`);
      const done = installed && state.ok && !state.missing.length;
      rows.ready.setDesc(done ? "✓ Open the Nuru panel to chat." : "After the steps above.");
      Object.assign(news, { nuru: "", ollama: "", models: "" });
      buttons.nuru.setButtonText(installed ? "Repair" : "Set up Nuru").setDisabled(false);
      buttons.ollama.setButtonText(action || "Install Ollama").setDisabled(!installed || !action);
      buttons.check.setDisabled(false);
      buttons.models.setDisabled(!state.ok || !state.missing.length);
      buttons.all.setDisabled(done || (installed && !state.ok && !action));
    };

    row("all", "Set up everything");
    // Clicking it is the user's go-ahead for all of it, Ollama included, so it says what that is first.
    rows.all.setDesc(`Runs the steps below that are still to do, in order: it installs Nuru, installs or updates Ollama ` +
      `(a separate app, ${windows ? "1.5 GB" : "200 MB"}) if needed, and downloads the models (a few GB).`);
    button("all", "Set up everything", async () => {
      if (!installed && !(await setUpNuru())) return this.display();
      installed = true;
      await refresh();
      // Start, then update if what started is older: at most both, each once.
      for (let tries = 0; !state.ok && tries < 2; tries++) {
        if (!ollamaAction() || !(await fixOllama())) break;
        await refresh();
      }
      if (!state.ok) return refresh(); // step 2 says why: what failed, or what to do
      if (state.missing.length) await downloadModels();
      return this.display();
    });
    row("nuru", "1. Nuru");
    button("nuru", installed ? "Repair" : "Set up Nuru", async () => { await setUpNuru(); this.display(); });
    row("ollama", "2. Ollama");
    button("check", "Check", async () => {}, "ollama");
    button("ollama", "Install Ollama", fixOllama);
    row("models", "3. Models");
    button("models", "Download", downloadModels);
    row("ready", "4. Ready");
    this.stepsChecked = refresh();
  }

  // Nuru's settings, read from and written to its env files; Nuru reads them when it starts.
  displayNuru(containerEl) {
    let settings;
    try {
      settings = new NuruSettings(readInstall(this.plugin.launcher.home).nuru);
    } catch {
      this.displaySteps(containerEl, false, ollama.CHAT_MODEL);
      return;
    }
    const chosen = settings.defaultModel() || ollama.CHAT_MODEL;
    this.displaySteps(containerEl, true, chosen.startsWith("anthropic:") ? null : chosen);
    new Setting(containerEl).setName("Nuru").setHeading();
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
