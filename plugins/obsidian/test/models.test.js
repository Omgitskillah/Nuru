"use strict";

// Settings' "Local models" section (lib/models.js, TODO F22): the models listed, a family's settings edited,
// saved, reset and reloaded, and a model added from Ollama, against a stand-in of nuru-serve's model
// settings routes (model-settings-v1) and of lib/ollama.js.
require("./helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");
const obsidian = require("obsidian");

const { displayModels } = require("../lib/models");
const { until } = require("./helpers/setup");

const QWEN = { model: "qwen3.5:4b", family: "qwen3.5", custom: false, revision: "initial", active_revision: "initial",
  header: "FROM __BASE_MODEL__\nPARAMETER temperature 1.0\n", profile: "think: {}\n", default: true,
  nuru: { header: "FROM __BASE_MODEL__\nPARAMETER temperature 1.0\n", profile: "think: {}\n" } };
const R1 = "1".repeat(32);
const R2 = "2".repeat(32);

class Refused extends Error {
  constructor(code, message) { super(message); this.code = code; }
}

// nuru-serve's model settings routes: `settings` per model, saved and reset as the service does; `refuse`
// makes the next call of that name fail.
function fakeClient(settings = { "qwen3.5:4b": QWEN }) {
  const client = {
    settings, calls: [], refuse: {},
    async modelSettings(model) { client.calls.push(["get", model]); return client.answer("get", () => client.settings[model]); },
    async saveModelSettings(body) {
      client.calls.push(["save", body]);
      return client.answer("save", () => (client.settings[body.model] = { ...client.settings[body.model], ...body, custom: true,
        revision: R2 }));
    },
    async resetModelSettings(model, revision) {
      client.calls.push(["reset", model, revision]);
      const current = client.settings[model];
      return client.answer("reset", () => (client.settings[model] = { ...current, custom: false, revision: R2,
        header: current.nuru?.header ?? null, profile: current.nuru?.profile ?? null }));
    },
    async addModel(model) { client.calls.push(["add", model]); return client.answer("add", () => client.added); },
    answer(name, then) {
      const refusal = client.refuse[name];
      delete client.refuse[name];
      if (refusal) throw refusal;
      return then();
    },
  };
  return client;
}

function fakeOllama({ running = true, have = ["qwen3.5:4b"], pull } = {}) {
  const fake = { have, pulled: [], progress: [],
    check: async (needed) => ({ running, missing: needed.filter((m) => !fake.have.includes(m)) }),
    pull: pull || (async (name, onProgress) => { onProgress(0, 0); onProgress(50, 100); fake.pulled.push(name); fake.have.push(name); }),
    filterPage: require("../lib/ollama").filterPage };
  return fake;
}

function section({ client = fakeClient(), choices, connect, ollama = fakeOllama() } = {}) {
  const containerEl = document.createElement("div");
  const remembered = [];
  const tab = {
    app: {}, ollama, removal: { remember: (home, what) => remembered.push([home, what]) },
    plugin: { launcher: { home: "/home/me" },
      connect: connect || (async () => ({ client, status: { models: { choices: choices || [
        { id: "qwen3.5:4b", label: "qwen3.5:4b", cloud: false },
        { id: "llama3.1:8b", label: "llama3.1:8b (untested)", cloud: false },
        { id: "anthropic:claude-haiku-5-5", label: "Claude Haiku 5.5", cloud: true }] } } })) },
  };
  displayModels(tab, containerEl);
  return { containerEl, client, ollama, remembered };
}

// A setting's row, as the stand-in Setting draws it: a div whose first child holds its name, the next its description.
const named = (el, name) => [...el.querySelectorAll("div")].find((row) => row.firstElementChild?.tagName === "DIV" &&
  row.firstElementChild.textContent === name && row.firstElementChild.children.length === 0);
const modal = () => obsidian.Modal.shown;
const dialogButton = (text) => [...modal().contentEl.querySelectorAll("button")].find((b) => b.textContent === text);
const said = () => modal().contentEl.querySelector(".nuru-model-said")?.textContent;
const areas = () => [...modal().contentEl.querySelectorAll("textarea")];

async function edit(label = "qwen3.5:4b", client = fakeClient()) {  // the row's label, as the picker shows it
  const { containerEl } = section({ client });
  await until(() => named(containerEl, label));
  named(containerEl, label).querySelector("button").click();
  await until(() => areas().length === 2);
  return client;
}

test.afterEach(() => { if (modal()) modal().close(); });

test("the section lists the local models Nuru offers, never Claude, and says which run on the user's settings", async () => {
  const { containerEl } = section();
  await until(() => named(containerEl, "qwen3.5:4b"));
  assert.ok(named(containerEl, "llama3.1:8b (untested)").textContent.includes("Nuru hasn't tested this one"));
  assert.equal(named(containerEl, "Claude Haiku 5.5"), undefined);
});

test("without Nuru running, or with no local model, it says so", async () => {
  const off = section({ connect: async () => { throw new Error("not running"); } });
  await until(() => off.containerEl.textContent.includes("Start Nuru to see and change its models."));
  const none = section({ choices: [{ id: "anthropic:claude-haiku-5-5", label: "Claude Haiku 5.5", cloud: true }] });
  await until(() => none.containerEl.textContent.includes("Nuru offers no local model yet."));
});

test("Edit shows the family's settings, says they cover every tag, and a save sends what was typed with its revision", async () => {
  const client = await edit();
  assert.ok(modal().contentEl.textContent.includes("every size and tag of qwen3.5: Nuru's own, to start from"));
  const [header, profile] = areas();
  assert.equal(header.value, QWEN.header);
  header.value = "FROM __BASE_MODEL__\nPARAMETER temperature 0.5\n";
  dialogButton("Save").click();
  await until(() => said() === "Saved: restart Nuru to use them.");
  assert.deepEqual(client.calls.at(-1), ["save", { model: "qwen3.5:4b", expected_revision: "initial",
    header: "FROM __BASE_MODEL__\nPARAMETER temperature 0.5\n", profile: profile.value }]);
  assert.ok(modal().contentEl.textContent.includes("Saved: restart Nuru to use them."), "saved, not yet what runs");
  assert.ok(dialogButton("Reset to Nuru's"), "now the user's own");
});

test("a refused save says why and keeps what was typed; one another window beat offers a reload", async () => {
  const client = fakeClient();
  await edit("qwen3.5:4b", client);
  areas()[0].value = "FROM __BASE_MODEL__\nSYSTEM mine\n";
  client.refuse.save = new Refused("invalid_request", "line 2: SYSTEM isn't allowed: Nuru's own prompt is always the system prompt");
  dialogButton("Save").click();
  await until(() => /SYSTEM isn't allowed/.test(said()));
  assert.equal(areas()[0].value, "FROM __BASE_MODEL__\nSYSTEM mine\n");
  assert.equal(dialogButton("Save").disabled, false, "the buttons work again");

  client.refuse.save = new Refused("conflict", "These settings changed since they were read: reload them.");
  dialogButton("Save").click();
  await until(() => /changed in another window/.test(said()));
  client.settings["qwen3.5:4b"] = { ...QWEN, custom: true, revision: R1, header: "FROM __BASE_MODEL__\n# theirs\n" };
  dialogButton("Reload").click();
  await until(() => areas()[0]?.value === "FROM __BASE_MODEL__\n# theirs\n");
  assert.deepEqual(client.calls.at(-1), ["get", "qwen3.5:4b"]);
});

test("a family on the user's settings resets to Nuru's, or for one Nuru has none for, is removed", async () => {
  const qwen = { ...QWEN, custom: true, revision: R1, active_revision: R1 };
  const llama = { ...QWEN, model: "llama3.1:8b", family: "llama3.1", custom: true, revision: R1, active_revision: R1, nuru: null, default: false };
  const client = fakeClient({ "qwen3.5:4b": qwen, "llama3.1:8b": llama });
  await edit("qwen3.5:4b", client);
  assert.ok(!modal().contentEl.querySelector(".nuru-model-pending"), "saved is what runs");
  dialogButton("Reset to Nuru's").click();
  await until(() => said() === "Back to Nuru's: restart Nuru to use them.");
  assert.deepEqual(client.calls.at(-1), ["reset", "qwen3.5:4b", R1]);
  modal().close();

  await edit("llama3.1:8b (untested)", client);
  client.refuse.reset = new Refused("conflict", "New conversations start on this model, and Nuru has no settings of its own for it: choose another default model first.");
  dialogButton("Remove my settings").click();
  await until(() => /choose another default model first/.test(said()));
  dialogButton("Remove my settings").click();
  await until(() => /no settings for llama3.1 now/.test(modal().contentEl.textContent));
  assert.equal(areas().length, 0, "nothing left to edit");
});

test("settings that can't be read say so", async () => {
  const client = fakeClient();
  client.refuse.get = new Refused("unreachable", "Nuru isn't answering. Is nuru-serve running?");
  const { containerEl } = section({ client });
  await until(() => named(containerEl, "qwen3.5:4b"));
  named(containerEl, "qwen3.5:4b").querySelector("button").click();
  await until(() => /Couldn't read them: Nuru isn't answering/.test(modal().contentEl.textContent));
});

// ---------------------------------------------------------------------- adding a model
function addRow(containerEl) {
  const row = named(containerEl, "Add a model");
  return { row, input: row.querySelector("input"), button: row.querySelector("button"),
    desc: () => row.children[1].textContent };
}

function type(input, value) {
  input.value = value;
  input.dispatchEvent(new window.Event("input"));
}

// POST /v1/models's answer, every field as model-settings-v1 has it.
const added = (fields) => ({ model: "llama3.1:8b", family: "llama3.1", custom: true, revision: R1, active_revision: R1,
  header: "FROM __BASE_MODEL__\n", profile: "think: {}\n", nuru: null, default: false, added: false, ...fields });

test("adding a model Ollama lacks downloads it, remembers it for Remove Nuru, then has Nuru set its family up", async () => {
  const client = fakeClient();
  client.added = added({ added: true, active_revision: "initial" });
  const { containerEl, ollama, remembered } = section({ client });
  const add = addRow(containerEl);
  type(add.input, "llama3.1:8b");
  add.button.click();
  await until(() => add.desc() === "Set up llama3.1: restart Nuru to use llama3.1:8b.");
  assert.deepEqual(ollama.pulled, ["llama3.1:8b"]);
  assert.deepEqual(remembered, [["/home/me", { model: "llama3.1:8b" }]]);
  assert.deepEqual(client.calls.at(-1), ["add", "llama3.1:8b"]);
});

test("a model Ollama has isn't downloaded again, and a family Nuru already runs is said so", async () => {
  const client = fakeClient();
  client.added = added({ model: "qwen3.5:9b", family: "qwen3.5", custom: false, revision: "initial", active_revision: "initial",
    nuru: { header: "FROM __BASE_MODEL__\n", profile: "think: {}\n" } });
  const { containerEl, ollama } = section({ client, ollama: fakeOllama({ have: ["qwen3.5:9b"] }) });
  const add = addRow(containerEl);
  type(add.input, "qwen3.5:9b");
  add.button.click();
  await until(() => add.desc() === "Nuru already runs qwen3.5 models: qwen3.5:9b is offered.");
  assert.deepEqual(ollama.pulled, []);
  client.added = added({});
  type(add.input, "llama3.1:8b");
  add.button.click();
  await until(() => add.desc() === "Nuru already runs llama3.1 models: llama3.1:8b is offered with your settings.");
});

test("a family added before but not yet in use says to restart, not that the model is offered (Astra's #64 review)", async () => {
  const client = fakeClient();
  client.added = added({ revision: R2, active_revision: "initial" });
  const { containerEl } = section({ client, ollama: fakeOllama({ have: ["llama3.1:8b"] }) });
  const add = addRow(containerEl);
  type(add.input, "llama3.1:8b");
  add.button.click();
  await until(() => add.desc() === "llama3.1 is set up already: restart Nuru to use llama3.1:8b.");
});

test("adding says what's wrong: not a name, no Ollama, a refusal, a blocked download", async () => {
  const client = fakeClient();
  const { containerEl } = section({ client, ollama: fakeOllama({ running: false }) });
  const add = addRow(containerEl);
  type(add.input, "Llama 3");
  add.button.click();
  await until(() => /isn't an Ollama model name/.test(add.desc()));
  type(add.input, "llama3.1:8b");
  add.button.click();
  await until(() => add.desc() === "Ollama isn't running: start it in step 2 above.");

  const refused = section({ client });
  const other = addRow(refused.containerEl);
  client.refuse.add = new Refused("invalid_request", "gemma4:e2b can't run Nuru: it can't use tools, and Nuru reads and writes your notes only through tools");
  type(other.input, "gemma4:e2b");
  refused.ollama.have.push("gemma4:e2b");
  other.button.click();
  await until(() => /can't use tools/.test(other.desc()));

  const blocked = section({ client, ollama: fakeOllama({ pull: async () => {
    throw new Error('pull model manifest: Get "https://gateway.zscaler.net/auth?x=1": blocked redirect to a different host');
  } }) });
  const filtered = addRow(blocked.containerEl);
  type(filtered.input, "llama3.1:8b");
  filtered.button.click();
  await until(() => /network filter \(gateway\.zscaler\.net\) stopped the download/.test(filtered.desc()));
  assert.ok(filtered.desc().includes('run "ollama pull llama3.1:8b" in a terminal, then click Add again.'));
  assert.ok(filtered.desc().includes("check with your IT"));
});
