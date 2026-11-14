// Search and Trash live outside the chat composer, preserving its draft and selection.
"use strict";
const { Modal } = require("obsidian");
const { newRequestId } = require("./protocol");
const PERMISSION = "Re-pair this device to generate titles, rename or delete conversations.";
class HistoryQuestion extends Modal {
  static ask(app, options) { return new Promise((resolve) => new HistoryQuestion(app, options, resolve).open()); }
  constructor(app, options, resolve) { super(app); this.options = options; this.resolve = resolve; }
  onOpen() {
    const { title, text, value, action } = this.options;
    this.setTitle(title); this.contentEl.createEl("p", { text });
    if (this.options.items) {
      const list = this.contentEl.createEl("ul", { cls: "nuru-history-targets" });
      for (const label of this.options.items) list.createEl("li", { text: label });
    }
    if (value !== undefined) {
      this.input = this.contentEl.createEl("input", { attr: { "aria-label": "Conversation name", maxlength: "200" } });
      this.input.value = value; this.input.focus();
    }
    this.contentEl.createEl("button", { text: action }).addEventListener("click", () => {
      this.resolve(this.input ? this.input.value : true); this.close();
    });
    const cancel = this.contentEl.createEl("button", { text: "Cancel" });
    cancel.addEventListener("click", () => this.close());
    if (!this.input) cancel.focus();
  }
  onClose() { this.resolve(null); this.contentEl.empty(); }
}
class HistoryModal extends Modal {
  constructor(app, client, options) {
    super(app); this.client = client; this.options = options;
    this.generation = 0; this.hidden = false; this.busy = false; this.closed = false;
  }
  onOpen() {
    this.setTitle("Conversation history");
    this.contentEl.addClass("nuru-history");
    this.search = this.contentEl.createEl("input", { attr: { type: "search", "aria-label": "Search titles and messages", maxlength: "256", placeholder: "Search titles and messages" } });
    this.search.addEventListener("input", () => this.load());
    this.contentEl.createEl("button", { text: "Clear" }).addEventListener("click", () => { this.search.value = ""; this.search.focus(); this.load(); });
    this.trash = this.contentEl.createEl("button", { text: "Trash", attr: { "aria-pressed": "false" } });
    this.trash.addEventListener("click", () => { this.hidden = !this.hidden; this.trash.setAttr("aria-pressed", String(this.hidden)); this.load(); });
    this.empty = this.contentEl.createEl("button", { text: "Empty trash" });
    this.empty.addEventListener("click", () => this.beginEmpty());
    this.contentEl.createEl("p", { text: this.options.writable ? "" : PERMISSION });
    this.note = this.contentEl.createEl("p", { attr: { role: "status" } });
    this.rows = this.contentEl.createDiv();
    this.more = this.contentEl.createEl("button", { text: "Load more" });
    this.more.addEventListener("click", () => this.load(true));
    this.retry = this.contentEl.createEl("button", { text: "Check pending erasure" });
    this.retry.addEventListener("click", () => this.run(() => this.settleEmpty()));
    this.buttons(); this.load();
  }
  onClose() { this.closed = true; this.generation++; this.contentEl.empty(); }
  buttons() {
    this.empty.hidden = !this.hidden;
    this.empty.disabled = this.busy || !this.options.writable || Boolean(this.options.getPending());
    this.retry.hidden = !this.options.getPending(); this.retry.disabled = this.busy || !this.options.writable;
    for (const button of this.rows.querySelectorAll("button")) {
      button.disabled = this.busy || (button.dataset.historyWrite === "true" && !this.options.writable);
      if (button.dataset.historyWrite === "true") button.title = this.options.writable ? "" : PERMISSION;
    }
    this.empty.title = this.options.writable ? "Permanently remove confirmed conversations from Nuru history" : PERMISSION;
  }
  async load(append = false) {
    const generation = ++this.generation;
    this.loading = true; this.more.disabled = true;
    try {
      const result = await this.client.history({ query: this.search.value, hidden: this.hidden, before: append ? this.cursor : null });
      if (this.closed || generation !== this.generation) return;
      if (!append) this.rows.empty();
      this.trash.setText(`Trash (${result.trash_count})`);
      this.trash.hidden = !this.hidden && result.trash_count === 0;
      for (const row of result.conversations) this.row(row);
      this.cursor = result.conversations.at(-1)?.id;
      this.more.hidden = result.conversations.length < 50; this.more.disabled = false;
    } catch (error) {
      if (!this.closed && generation === this.generation) this.note.setText(`Couldn't load history: ${error.message}`);
    } finally { if (generation === this.generation) this.loading = false; }
    if (!this.closed) this.buttons();
  }
  row(row) {
    const el = this.rows.createDiv({ cls: "nuru-history-row" });
    el.createEl("span", { text: row.title || "Untitled conversation" });
    const action = (text, fn, write = true) => {
      const button = el.createEl("button", { text });
      button.dataset.historyWrite = String(write);
      button.disabled = write && !this.options.writable;
      if (button.disabled) button.title = PERMISSION;
      button.addEventListener("click", () => { if (!button.disabled) this.run(fn); });
    };
    if (this.hidden) {
      action("Restore", async () => { await this.client.changeHistory(row.id, "undo"); this.note.setText("Conversation restored."); });
    } else {
      action("Open", async () => { this.close(); await this.options.openConversation(row.id); }, false);
      action("Rename", async () => {
        const title = await HistoryQuestion.ask(this.app, { title: "Rename conversation", text: "Choose a name (up to 200 characters).", value: row.title || "", action: "Rename" });
        if (title === null || this.closed) return;
        await this.client.changeHistory(row.id, "rename", { title, title_rev: row.title_rev }); this.note.setText("Conversation renamed.");
      });
      action("Delete", async () => {
        const yes = await HistoryQuestion.ask(this.app, { title: "Move conversation to Trash?", text: "This hides the conversation. Restore it at any time until you choose Empty trash. Vault notes stay unchanged.", action: "Move to Trash" });
        if (!yes || this.closed) return;
        await this.client.changeHistory(row.id, "hide"); this.note.setText("Moved to Trash. Open Trash to restore it.");
      });
    }
  }
  async run(action) {
    if (this.busy || this.loading || this.closed) return;
    this.busy = true; this.buttons();
    try { await action(); await this.options.changed(); if (!this.closed) await this.load(); }
    catch (error) {
      if (error.status === 403) this.options.writable = false;
      if (!this.closed) this.note.setText(error.status === 403 ? PERMISSION : `Couldn't finish: ${error.message}. Refresh history before trying again.`);
    } finally { this.busy = false; if (!this.closed) this.buttons(); }
  }
  async beginEmpty() {
    if (!this.options.writable || this.options.getPending()) return;
    return this.run(async () => {
      const targets = [], labels = [], seen = new Set(); let before = null;
      do {
        const page = await this.client.history({ hidden: true, before });
        if (this.closed) return;
        for (const row of page.conversations) {
          if (seen.has(row.id)) throw new Error("History changed during pagination; refresh it");
          seen.add(row.id); targets.push({ id: row.id, trash_rev: row.trash_rev });
          labels.push(row.title || "Untitled conversation");
        }
        before = page.conversations.length === 50 ? page.conversations.at(-1).id : null;
      } while (before);
      if (!targets.length) { this.note.setText("Trash is empty."); return; }
      const yes = await HistoryQuestion.ask(this.app, { title: `Permanently erase ${targets.length} conversations?`,
        text: "This empties all Trash, including conversations outside the current search. This can't be undone. Your vault notes aren't changed. Nuru keeps a record of the changes it made to your notes, and its logs and your computer's backups aren't erased. Anything restored, changed or busy will be kept.", action: "Empty trash permanently", items: labels });
      if (!yes || this.closed) return;
      if (this.options.getPending()) throw new Error("Another erasure needs settlement first");
      await this.options.savePending({ remaining: targets, active: null, erased: 0, skipped: 0 });
      await this.settleEmpty();
    });
  }
  async settleEmpty() {
    let state = JSON.parse(JSON.stringify(this.options.getPending() || null));
    if (!state) return;
    while (state.active || state.remaining.length) {
      if (this.closed) return;
      if (!state.active) {
        state = { ...state, active: { requestId: newRequestId(), targets: state.remaining.slice(0, 200) }, remaining: state.remaining.slice(200) };
        await this.options.savePending(state);
      }
      const result = await this.client.emptyTrash(state.active.targets, state.active.requestId);
      if (result.state !== "completed") {
        this.note.setText(`Erased ${state.erased + result.erased}; the rest of this batch is pending. Check pending erasure to retry when Nuru is ready.`); return;
      }
      state = { ...state, active: null, erased: state.erased + result.erased, skipped: state.skipped + result.skipped.length };
      await this.options.savePending(state);
    }
    await this.options.savePending(null);
    this.note.setText(`Erased ${state.erased}; kept ${state.skipped} restored, changed, unavailable or busy conversations.`);
  }
}
module.exports = { HistoryModal, HistoryQuestion, PERMISSION };
