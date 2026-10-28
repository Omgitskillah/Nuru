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
  constructor(app, client, model) {
    super(app);
    this.client = client;
    this.model = model;
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
  tab.plugin.connect().then(({ client, status }) => {
    const local = (status.models?.choices || []).filter((choice) => !choice.cloud);
    if (!local.length) list.createEl("p", { cls: "setting-item-description", text: "Nuru offers no local model yet." });
    for (const choice of local) {
      new Setting(list).setName(choice.label).setDesc(choice.label.endsWith("(untested)") ? "Your settings: Nuru hasn't tested this one." : "")
        .addButton((button) => button.setButtonText("Edit").onClick(() => new ModelSettingsModal(tab.app, client, choice.id).open()));
    }
  }).catch(() => {
    list.createEl("p", { cls: "setting-item-description", text: "Start Nuru to see and change its models." });
  });

  const add = new Setting(containerEl).setName("Add a model");
  const say = (text) => { add.setDesc(text); }; // returns nothing: see the top of this file
  say("An Ollama model name, such as llama3.1:8b. Nuru downloads it if needed, checks that it can use tools, " +
    "and offers it after a restart.");
  let typed = "";
  add.addText((text) => text.setPlaceholder("llama3.1:8b").onChange((value) => { typed = value.trim(); }));
  add.addButton((button) => button.setButtonText("Add").onClick(async () => {
    const name = typed;
    if (!commandSafe(name)) return say("That isn't an Ollama model name: lowercase, like llama3.1:8b.");
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
    }
  }));
}

module.exports = { ModelSettingsModal, displayModels };
