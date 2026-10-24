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
for (const name of ["createEl", "appendText"]) window.DocumentFragment.prototype[name] = proto[name];
globalThis.createFragment = (build) => {
  const fragment = window.document.createDocumentFragment();
  if (build) build(fragment);
  return fragment;
};
globalThis.createDiv = (options) => make("div", options);
globalThis.createEl = (tag, options) => make(tag, options);

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

  registerEvent(ref) { this.register(() => ref.off()); }

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

  setDesc(desc) {
    this.descEl ??= this.settingEl.createDiv();
    if (typeof desc === "string") this.descEl.setText(desc);
    else this.descEl.replaceChildren(desc);
    return this;
  }

  setHeading() { return this; }

  addText(build) {
    const input = this.settingEl.createEl("input");
    build({ inputEl: input, setValue(v) { input.value = v; return this; }, setPlaceholder(p) { input.placeholder = p; return this; },
      onChange(f) { input.addEventListener("input", () => f(input.value)); return this; } });
    return this;
  }

  addToggle(build) {
    const input = this.settingEl.createEl("input");
    input.type = "checkbox";
    build({ setValue(v) { input.checked = v; return this; }, onChange(f) { input.addEventListener("change", () => f(input.checked)); return this; } });
    return this;
  }

  addDropdown(build) {
    const select = this.settingEl.createEl("select");
    build({ selectEl: select, addOption(value, text) { select.createEl("option", { text, value }); return this; },
      setValue(v) { select.value = v; return this; }, onChange(f) { select.addEventListener("change", () => f(select.value)); return this; } });
    return this;
  }

  addButton(build) {
    const button = this.settingEl.createEl("button");
    build({ setButtonText(t) { button.textContent = t; return this; }, onClick(f) { button.addEventListener("click", f); return this; },
      setDisabled(d) { button.disabled = d; return this; } });
    return this;
  }
}

class Notice {
  constructor(message) { Notice.shown.push(message); }
}
Notice.shown = [];

class MarkdownView {}

// Obsidian's context menu: here it records its items, and the last one shown, for a test to pick from.
class Menu {
  constructor() { this.items = []; }
  addItem(build) {
    const item = { title: "", checked: false, click: null };
    const api = { setTitle(t) { item.title = t; return api; }, setChecked(c) { item.checked = c; return api; },
      onClick(f) { item.click = f; return api; }, setIcon() { return api; } };
    build(api);
    this.items.push(item);
    return this;
  }
  showAtMouseEvent() { Menu.shown = this; }
  pick(title) { this.items.find((i) => i.title === title).click(); }
}
Menu.shown = null;

// Obsidian draws a Lucide icon; here the element only records which one.
function setIcon(el, name) { el.empty(); el.dataset.icon = name; }

const Keymap = { isModEvent: (event) => Boolean(event && (event.metaKey || event.ctrlKey)) };

// Obsidian's requestUrl; a test sets obsidian.requestUrl.handler to answer it.
async function requestUrl(request) { return requestUrl.handler(request); }
requestUrl.handler = async () => ({ status: 200 });

module.exports = { requestUrl, Component, ItemView, Plugin, PluginSettingTab, Setting, Notice, MarkdownView, Menu, Keymap, setIcon,
  window };
