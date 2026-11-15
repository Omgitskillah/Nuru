// F40: pending edits are local to this popover. Only an explicit Measure or Apply can reload.
"use strict";
const HINT = "How much memory Nuru may use to remember this conversation. More lets longer conversations and bigger notes fit, but takes memory from the rest of your computer; too much can slow everything down. Changing it reloads the model.";
const integer = (n) => Number.isSafeInteger(n) && n > 0;
function checked(info, model) {
  if (!info || info.model !== model || typeof info.revision !== "string" || !integer(info.window_tokens) ||
      info.window_tokens > 16777216 || typeof info.measured !== "boolean" || typeof info.can_measure !== "boolean" ||
      typeof info.busy !== "boolean" || ["reason", "notice", "limit_reason"].some((key) => info[key] != null && typeof info[key] !== "string")) throw new Error("Invalid context settings from Nuru");
  if (info.measured && (!integer(info.gb) || !integer(info.min_gb) || !integer(info.max_gb) ||
      info.min_gb > info.gb || info.gb > info.max_gb || info.max_gb > 4096 || !integer(info.max_tokens) ||
      !Number.isFinite(info.bytes_per_token) || info.bytes_per_token <= 0)) throw new Error("Invalid context memory limits from Nuru");
  return info;
}
class ContextPanel {
  constructor(anchor, { client, model, label, usage, busy = () => false, current = () => true, reloading = () => {} }) {
    Object.assign(this, { anchor, client, model, label, usage, busy, current, reloading });
    this.anchor.setAttr("aria-expanded", "true");
    this.closed = false; this.working = false; this.failed = false; this.document = anchor.ownerDocument;
    this.el = anchor.parentElement.createDiv({ cls: "nuru-context-panel", attr: { role: "dialog", "aria-label": `Context window · ${label}` } });
    this.el.createEl("h3", { text: `Context window · ${label}` });
    this.el.createEl("p", { text: usage || "Used now: unavailable", cls: "nuru-context-muted" });
    this.body = this.el.createDiv({ cls: "nuru-context-panel-body" });
    this.note = this.el.createEl("p", { attr: { role: "status", "aria-live": "polite" } });
    this.closeButton = this.el.createEl("button", { text: "Close", attr: { "aria-label": "Close context window settings" } });
    this.closeButton.addEventListener("click", () => this.close());
    this.outside = (event) => { if (!this.el.contains(event.target) && !this.anchor.contains(event.target)) this.close(false); };
    this.keys = (event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); this.close(); } };
    this.document.addEventListener("pointerdown", this.outside);
    this.el.addEventListener("keydown", this.keys);
    this.closeButton.focus();
    this.ready = this.load();
  }
  close(focus = true) {
    if (this.closed) return;
    this.anchor.setAttr("aria-expanded", "false");
    this.closed = true; this.document.removeEventListener("pointerdown", this.outside);
    this.el.removeEventListener("keydown", this.keys); this.el.remove();
    if (focus && this.anchor.isConnected) this.anchor.focus();
  }
  async load() {
    this.note.setText("Reading context settings…");
    try {
      const info = checked(await this.client.contextSettings(this.model), this.model);
      if (this.closed || !this.current()) return;
      this.info = info; this.pending = info.gb; this.note.setText(""); this.render();
    } catch (error) { if (!this.closed) this.note.setText(error.message); }
  }
  render() {
    this.body.empty();
    const info = this.info, disabled = this.working || this.failed || info.busy || this.busy();
    const row = this.body.createDiv({ cls: "nuru-context-size" });
    this.minus = row.createEl("button", { text: "−", attr: { "aria-label": "Use 1 GB less context memory" } });
    const size = row.createDiv();
    size.createEl("strong", { text: info.measured ? `${this.pending} GB` : `${info.window_tokens.toLocaleString()} tokens` });
    const tokens = info.measured ? Math.min(info.max_tokens, Math.floor(this.pending * 1e9 / info.bytes_per_token)) : null;
    size.createEl("div", { text: info.measured ? `about ${tokens.toLocaleString()} tokens` : "Memory use not measured yet", cls: "nuru-context-muted" });
    this.plus = row.createEl("button", { text: "+", attr: { "aria-label": "Use 1 GB more context memory" } });
    this.minus.disabled = !info.measured || disabled || this.pending <= info.min_gb;
    this.plus.disabled = !info.measured || disabled || this.pending >= info.max_gb;
    this.minus.addEventListener("click", () => { this.pending--; this.render(); this.minus.focus(); });
    this.plus.addEventListener("click", () => { this.pending++; this.render(); this.plus.focus(); });
    if (info.measured) {
      this.body.createEl("p", { text: `Min ${info.min_gb} GB · Max ${info.max_gb} GB`, cls: "nuru-context-muted" });
      if (info.limit_reason) this.body.createEl("p", { text: info.limit_reason, cls: "nuru-context-muted" });
      this.body.createEl("p", { text: HINT });
    } else if (info.can_measure) {
      this.body.createEl("p", { text: "Measure memory use before changing the size. Nuru briefly loads the model at three bounded sizes, then puts it back. This takes about a minute; chat waits. It cannot run while a reply is in progress." });
      this.body.createEl("p", { text: "Only when you press it. Never in the background.", cls: "nuru-context-muted" });
    }
    if (info.reason) this.body.createEl("p", { text: info.reason });
    this.body.createEl("p", { text: `Applies to every ${this.label} conversation`, cls: "nuru-context-muted" });
    this.action = this.body.createEl("button", { text: this.working ? (this.operation === "measure" ? "Measuring…" : "Reloading the model…") : info.measured ? "Apply" : "Measure memory use", cls: "mod-cta" });
    this.action.disabled = disabled || (info.measured ? this.pending === info.gb : !info.can_measure);
    this.action.addEventListener("click", () => this.run(info.measured ? "apply" : "measure"));
    if (disabled && !this.working && !this.failed) this.note.setText("Nuru is busy. Wait until its replies and title work finish.");
  }
  async run(operation) {
    if (this.closed || this.working || this.failed || !this.current()) return;
    if (this.busy() || this.info.busy) { this.note.setText("Nuru is busy. Wait until its replies and title work finish."); return; }
    if (operation === "measure" && !this.info.can_measure) return;
    if (operation === "apply" && this.pending === this.info.gb) return;
    this.working = true; this.operation = operation; this.render();
    this.note.setText(operation === "measure" ? "Measuring…" : "Reloading the model…");
    try {
      this.reloading();
      const result = checked(await this.client.changeContext(this.model, { operation, expected_revision: this.info.revision,
        ...(operation === "apply" ? { gb: this.pending } : {}) }), this.model);
      if (this.closed || !this.current()) return;
      this.info = result; this.pending = result.gb; this.note.setText(result.notice || (operation === "measure" ? "Memory use measured." : "Context size applied."));
    } catch (error) {
      this.failed = true;
      if (!this.closed && this.current()) this.note.setText(`${error.message} Reopen this panel to check the current setting before retrying.`);
    } finally {
      this.working = false;
      if (!this.closed && this.current()) this.render();
    }
  }
}
module.exports = { ContextPanel, checked };
