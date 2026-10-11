// A stand-in for the `obsidian` module, over jsdom: enough of its API, and of the helpers it adds to
// elements (createEl, empty, setText...), for the plugin's code to run in node --test. It is not
// Obsidian: what only real Obsidian can show is checked by hand (nuru/evals/obsidian_remote/).
// Required through test/helpers/setup.js, which makes `require("obsidian")` load this.
"use strict";

const { JSDOM } = require("jsdom");

const dom = new JSDOM("<!doctype html><html><body></body></html>", { pretendToBeVisual: true });
const { window } = dom;
for (const name of ["window", "document", "HTMLElement", "Node", "Event", "MouseEvent", "KeyboardEvent"]) {
  globalThis[name] = name === "window" ? window : window[name];
}
window.open = (url) => { window.opened = [...(window.opened || []), url]; };

function make(tag, options = {}, parent) {
  const el = window.document.createElement(tag);
  const classes = Array.isArray(options.cls) ? options.cls : options.cls ? options.cls.split(" ") : [];
  if (classes.length) el.classList.add(...classes.filter(Boolean));
  if (options.text !== undefined) el.textContent = options.text;
  if (options.value !== undefined) el.value = options.value;
  for (const [key, value] of Object.entries(options.attr || {})) el.setAttribute(key, value);
  if (parent) parent.appendChild(el);
  return el;
}

const proto = window.HTMLElement.prototype;
proto.createEl = function (tag, options) { return make(tag, options, this); };
proto.createDiv = function (options) { return make("div", options, this); };
proto.createSpan = function (options) { return make("span", options, this); };
proto.empty = function () { this.replaceChildren(); };
proto.setText = function (text) { this.textContent = text; };
proto.appendText = function (text) { this.appendChild(window.document.createTextNode(text)); };
proto.addClass = function (...names) { this.classList.add(...names); };
proto.removeClass = function (...names) { this.classList.remove(...names); };
proto.toggleClass = function (name, on) { this.classList.toggle(name, on); };
proto.setAttr = function (name, value) { this.setAttribute(name, value); };
globalThis.createDiv = (options) => make("div", options);

class Component {
  constructor() {
    this._children = [];
    this._cleanups = [];
    this._loaded = false;
  }

  load() { this._loaded = true; for (const child of this._children) child.load(); }

  unload() {
    for (const child of this._children.splice(0)) child.unload();
    for (const cleanup of this._cleanups.splice(0)) cleanup();
    this._loaded = false;
  }

  addChild(child) { this._children.push(child); if (this._loaded) child.load(); return child; }

  removeChild(child) {
    const at = this._children.indexOf(child);
    if (at >= 0) { this._children.splice(at, 1); child.unload(); }
    return child;
  }

  register(cleanup) { this._cleanups.push(cleanup); }

  registerDomEvent(el, type, handler) {
    el.addEventListener(type, handler);
    Component.listeners += 1;
    this.register(() => { el.removeEventListener(type, handler); Component.listeners -= 1; });
  }

  registerInterval(id) {
    Component.intervals += 1;
    this.register(() => { window.clearInterval(id); Component.intervals -= 1; });
    return id;
  }
}
Component.listeners = 0; // registered and not yet released, across every component
Component.intervals = 0;

class ItemView extends Component {
  constructor(leaf) {
    super();
    this.leaf = leaf;
    this.app = leaf.app;
    this.containerEl = make("div");
    this.contentEl = this.containerEl.createDiv();
  }

  // What Obsidian does to open and close a view.
  async open() { this.load(); await this.onOpen(); }

  async close() { await this.onClose(); this.unload(); }
}

class Plugin extends Component {
  constructor(app, manifest) { super(); this.app = app; this.manifest = manifest; this._data = null; }

  async loadData() { return this._data; }

  async saveData(data) { this._data = JSON.parse(JSON.stringify(data)); }

  registerView(type, factory) { this.app.views = { ...(this.app.views || {}), [type]: factory }; }

  addRibbonIcon() { return make("div"); }

  addCommand(command) { this.app.commands = [...(this.app.commands || []), command]; }

  addSettingTab(tab) { this.app.settingTabs = [...(this.app.settingTabs || []), tab]; }
}

class PluginSettingTab {
  constructor(app, plugin) { this.app = app; this.plugin = plugin; this.containerEl = make("div"); }
}

class Setting {
  constructor(containerEl) { this.settingEl = containerEl.createDiv(); }

  setName(name) { this.settingEl.createDiv({ text: name }); return this; }

  setDesc(desc) { this.settingEl.createDiv({ text: desc }); return this; }

  addText(build) {
    const input = this.settingEl.createEl("input");
    build({ setValue(v) { input.value = v; return this; }, onChange(f) { input.addEventListener("input", () => f(input.value)); return this; } });
    return this;
  }

  addButton(build) {
    const button = this.settingEl.createEl("button");
    build({ setButtonText(t) { button.textContent = t; return this; }, onClick(f) { button.addEventListener("click", f); return this; } });
    return this;
  }
}

class Notice {
  constructor(message) { Notice.shown.push(message); }
}
Notice.shown = [];

class MarkdownView {}

const Keymap = { isModEvent: (event) => Boolean(event && (event.metaKey || event.ctrlKey)) };

module.exports = { Component, ItemView, Plugin, PluginSettingTab, Setting, Notice, MarkdownView, Keymap,
  window };
