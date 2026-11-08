// Settings' "Local models" section (TODO F22): the local models Nuru offers, each one's settings to edit,
// and adding a model from Ollama's library. nuru-serve checks, builds and saves them (its model settings
// routes, model-settings-v1.schema.json); this only shows them and passes on what the user typed. What is
// saved applies when Nuru restarts. Nothing here returns an Obsidian Setting from a promise: a Setting has
// a then(), and a promise resolved with one never settles (Obsidian freezes).
"use strict";

const { Modal, Setting } = require("obsidian");

const { commandSafe } = require("./remove");

// One family's settings, to read, change and save, or reset to Nuru's.
class ModelSettingsModal extends Modal {
  constructor(app, client, model, changed = async () => {}) {
    super(app);
    this.client = client;
    this.model = model;
    this.changed = changed;
  }

  onOpen() {
    this.setTitle(`Settings for ${this.model}`);
    this.load();
  }

  onClose() {
    this.contentEl.empty();
  }

  async load(note = "") {
    let settings;
    try {
      settings = await this.client.modelSettings(this.model);
    } catch (error) {
      this.contentEl.empty();
      this.contentEl.createEl("p", { text: `Couldn't read them: ${error.message}` });
      return;
    }
    this.show(settings, note);
  }

  show(settings, note = "") {
    const el = this.contentEl;
    el.empty();
    el.createEl("p", { text: `These apply to every size and tag of ${settings.family}` +
      (settings.custom ? ", and they are yours, not Nuru's." : ": Nuru's own, to start from.") });
    if (settings.revision !== settings.active_revision) {
      el.createEl("p", { cls: "nuru-model-pending", text: "Saved: restart Nuru to use them." });
    }
    if (settings.header === null) {
      el.createEl("p", { text: `Nuru has no settings for ${settings.family} now: add one of its models to set it up again.` });
      return;
    }
    el.createEl("h4", { text: "Modelfile header" });
    el.createEl("p", { cls: "setting-item-description", text: "How Ollama runs the model: FROM __BASE_MODEL__, then " +
      "PARAMETER, TEMPLATE, CAPABILITY and REQUIRES lines. Nuru adds its own prompt." });
    const header = el.createEl("textarea", { cls: "nuru-model-text", attr: { rows: 12, spellcheck: "false" } });
    header.value = settings.header;
    el.createEl("h4", { text: "Thinking and sampling" });
    el.createEl("p", { cls: "setting-item-description", text: "What Nuru sends with each request (YAML): think, default and sampling." });
    const profile = el.createEl("textarea", { cls: "nuru-model-text", attr: { rows: 8, spellcheck: "false" } });
    profile.value = settings.profile;
    const said = el.createEl("p", { cls: "nuru-model-said",
      text: note || "Saving checks them and has Ollama build the model once; they apply when Nuru restarts." });
    const buttons = el.createDiv({ cls: "nuru-model-buttons" });
    const button = (text, act) => {
      const b = buttons.createEl("button", { text });
      b.addEventListener("click", async () => {
        for (const other of buttons.querySelectorAll("button")) other.disabled = true;
        await act();
        for (const other of buttons.querySelectorAll("button")) other.disabled = false;
      });
      return b;
    };
    const tell = (error) => {
      said.setText(error.code === "conflict" && /reload/.test(error.message)
        ? "They changed in another window meanwhile: reload to see them, then make your change again."
        : error.message);
      if (error.code === "conflict" && !buttons.querySelector(".nuru-model-reload")) {
        button("Reload", () => this.load()).classList.add("nuru-model-reload");
      }
    };
    button("Save", async () => {
      said.setText("Checking with Ollama…");
      try {
        const saved = await this.client.saveModelSettings({ model: this.model, expected_revision: settings.revision,
          header: header.value, profile: profile.value });
        this.show(saved, "Saved: restart Nuru to use them.");
        await this.changed();
      } catch (error) {
        tell(error);
      }
    }).classList.add("mod-cta");
    if (settings.custom) {
      button(settings.nuru ? "Reset to Nuru's" : "Remove my settings", async () => {
        try {
          const reset = await this.client.resetModelSettings(this.model, settings.revision);
          this.show(reset, settings.nuru ? "Back to Nuru's: restart Nuru to use them."
            : "Removed: after a restart Nuru no longer offers it.");
          await this.changed();
        } catch (error) {
          tell(error);
        }
      }).classList.add("mod-warning");
    }
  }
}

// The section, under `containerEl`. `tab` is the settings tab: its plugin (connect, launcher), ollama
// (check, pull, filterPage) and removal (what Nuru downloaded, for Remove Nuru).
function displayModels(tab, containerEl) {
  new Setting(containerEl).setName("Local models").setHeading();
  const list = containerEl.createDiv({ cls: "nuru-models" });
  let refreshId = 0;
  const refresh = async () => {
    const id = ++refreshId;
    try {
      const { client, status } = await tab.plugin.connect();
      const { pending } = await client.pendingModelSettings();
      if (id !== refreshId) return;
      list.empty();
      const local = (status.models?.choices || []).filter((choice) => !choice.cloud);
      const pendingFamilies = new Set(pending.map((row) => row.family));
      if (!local.length && !pending.length) list.createEl("p", { cls: "setting-item-description", text: "Nuru offers no local model yet." });
      const changed = async () => {
        await refresh();
        await tab.plugin.modelsChanged();
      };
      for (const choice of local) {
        const family = choice.id.split("/").pop().split(":")[0];
        if (pendingFamilies.has(family)) continue; // a pending row includes every installed tag
        new Setting(list).setName(choice.label).setDesc(choice.label.endsWith("(untested)") ? "Your settings: Nuru hasn't tested this one." : "")
          .addButton((button) => button.setButtonText("Edit").onClick(() => new ModelSettingsModal(tab.app, client, choice.id, changed).open()));
      }
      for (const row of pending) {
        const known = local.some((choice) => choice.id.split("/").pop().split(":")[0] === row.family);
        const message = row.removed ? "Removed: after a restart Nuru no longer offers it."
          : known ? "Saved: restart Nuru to use these settings." : "Added: restart Nuru to use it.";
        const setting = new Setting(list).setName(row.family)
          .setDesc(`${row.models.length ? row.models.join(", ") + ": " : ""}${message}`);
        if (row.models.length) setting.addButton((button) => button.setButtonText("Edit").onClick(() =>
          new ModelSettingsModal(tab.app, client, row.models[0], changed).open()));
        setting.addButton((button) => button.setButtonText("Restart Nuru").onClick(async () => {
          button.setDisabled(true);
          try {
            await tab.plugin.launcher.restart(); // refuses busy turns; never kills one silently
            for (const view of tab.plugin.views()) await view.reconnect();
            await changed();
          } catch (error) {
            setting.setDesc(`Couldn't restart Nuru: ${error.message}`);
          } finally {
            button.setDisabled(false);
          }
        }));
      }
    } catch (error) {
      if (id !== refreshId) return;
      list.empty();
      list.createEl("p", { cls: "setting-item-description", text: `Start Nuru to see and change its models. Couldn't refresh: ${error.message}` });
    }
  };
  void refresh();

  const add = new Setting(containerEl).setName("Add a model");
  const say = (text) => { add.setDesc(text); }; // returns nothing: see the top of this file
  say("An Ollama model name, such as llama3.1:8b. Nuru downloads it if needed, checks that it can use tools, " +
    "and offers it after a restart.");
  let typed = "";
  add.addText((text) => text.setPlaceholder("llama3.1:8b").onChange((value) => { typed = value.trim(); }));
  add.addButton((button) => button.setButtonText("Add").onClick(async () => {
    const name = typed;
    if (!commandSafe(name)) return say("That isn't an Ollama model name: lowercase, like llama3.1:8b.");
    button.setDisabled(true);
    try {
      say("Checking Ollama…");
      const found = await tab.ollama.check([name]);
      if (!found.running) return say("Ollama isn't running: start it in step 2 above.");
      if (found.missing.length) {
        await tab.ollama.pull(name, (done, total) => say(total ? `Downloading ${name}: ${Math.floor((100 * done) / total)}%`
          : `Downloading ${name}…`));
        tab.removal.remember(tab.plugin.launcher.home, { model: name });
      }
      say(`Checking ${name} with Nuru…`);
      const { client } = await tab.plugin.connect();
      const result = await client.addModel(name);
      // Offered only once this service runs with what is saved: added now, or saved earlier but not yet in use.
      const pending = result.revision !== result.active_revision;
      await refresh();
      await tab.plugin.modelsChanged();
      say(result.added ? `Set up ${result.family}: restart Nuru to use ${name}.`
        : pending ? `${result.family} is set up already: restart Nuru to use ${name}.`
          : `Nuru already runs ${result.family} models: ${name} is offered${result.custom ? " with your settings" : ""}.`);
    } catch (error) {
      const filter = tab.ollama.filterPage(error.message);
      if (filter) {
        return say(`A network filter${filter.host ? ` (${filter.host})` : ""} stopped the download. It is often an ` +
          `organisation's: check with your IT before going around it. If you may, ${filter.page ? `open ${filter.page} in ` +
          "your browser, or " : ""}run "ollama pull ${name}" in a terminal, then click Add again.`);
      }
      say(error.message);
    } finally {
      button.setDisabled(false);
    }
  }));
}

module.exports = { ModelSettingsModal, displayModels };
