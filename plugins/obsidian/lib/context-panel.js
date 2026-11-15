// F40: pending edits are local to this popover. Only an explicit Measure or Apply can reload.
"use strict";
const HINT = "How much memory Nuru may use to remember this conversation. More lets longer conversations and bigger notes fit, but takes memory from the rest of your computer; too much can slow everything down. Changing it reloads the model.";
const integer = (n) => Number.isSafeInteger(n) && n > 0;
function checked(info, model) {
  if (!info || info.model !== model || typeof info.revision !== "string" || !integer(info.window_tokens) ||
      info.window_tokens > 16777216 || typeof info.measured !== "boolean" || typeof info.can_measure !== "boolean" ||
      typeof info.busy !== "boolean" || (info.needs_apply != null && typeof info.needs_apply !== "boolean") || ["reason", "notice", "limit_reason"].some((key) => info[key] != null && typeof info[key] !== "string")) throw new Error("Invalid context settings from Nuru");
  if (info.measured && (!integer(info.gb) || !integer(info.min_gb) || !integer(info.max_gb) ||
      info.min_gb > info.gb || info.gb > info.max_gb || info.max_gb > 4096 || !integer(info.max_tokens) ||
      !Number.isFinite(info.bytes_per_token) || info.bytes_per_token <= 0)) throw new Error("Invalid context memory limits from Nuru");
  return info;
}
function checkedRecovery(info) {
  if (!info || typeof info.reserved !== "boolean" ||
      (info.reserved && typeof info.owner_running !== "boolean") ||
      (info.run_id != null && !/^[0-9a-f]{32}$/.test(info.run_id)) ||
      (info.recovery != null && (typeof info.recovery !== "string" || info.recovery.length > 1000))) throw new Error("Invalid recovery status from Nuru");
  if ((info.state != null && !["busy", "uncertain", "abandoned"].includes(info.state)) ||
      (info.own != null && typeof info.own !== "boolean")) throw new Error("Invalid recovery status from Nuru");
  return info;
}
class ContextPanel {
  constructor(anchor, { client, model, label, usage, busy = () => false, current = () => true, reloading = () => {}, recoveryChanged = () => {} }) {
    Object.assign(this, { anchor, client, model, label, usage, busy, current, reloading, recoveryChanged });
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
      const recovery = this.client.contextRecovery ? await this.client.contextRecovery() : { reserved: false };
      if (this.closed || !this.current()) return;
      checkedRecovery(recovery);
      this.recoveryChanged(recovery.reserved, recovery.state === "busy"); this.recoveryReserved = recovery.reserved;
      if (recovery.reserved) { this.renderRecovery(recovery); return; }
      const info = checked(await this.client.contextSettings(this.model), this.model);
      if (this.closed || !this.current()) return;
      this.info = info; this.pending = info.gb; this.failed = false; this.note.setText(""); this.render();
    } catch (error) { if (!this.closed) this.note.setText(error.message); }
  }
  renderRecovery(info) {
    this.body.empty(); this.note.setText(""); this.restarted = false;
    if (info.state === "busy") {
      this.body.createEl("p", { text: info.own ? "Nuru is measuring or reloading a model. Chat waits until it finishes." : "Another Nuru is measuring or reloading a model. Wait for it to finish." });
      const check = this.body.createEl("button", { text: "Check again" });
      check.addEventListener("click", () => this.load());
      return;
    }
    this.body.createEl("p", { text: info.owner_running === false ? "⚠ Nuru is paused: a model load was interrupted" : "⚠ Nuru is paused: local model compute is reserved", cls: "nuru-context-warning" });
    this.body.createEl("p", { text: "Nuru can't tell whether Ollama is still loading. Messages wait until this is cleared. Your notes and chats are not affected." });
    if (info.recovery && !info.run_id) this.body.createEl("p", { text: info.recovery });
    const stopped = info.owner_running === false;
    this.body.createEl("p", { text: stopped ? "✓ The Nuru that started it has stopped" : "The Nuru that started it is still running or its ownership could not be checked. Quit and reopen Obsidian to restart Nuru, then check again." });
    const label = this.body.createEl("label");
    const checkbox = label.createEl("input", { attr: { type: "checkbox" } });
    label.createEl("span", { text: "I've restarted Ollama" });
    this.body.createEl("p", { text: "Quit Ollama, then open it again. On macOS, use its menu bar icon.", cls: "nuru-context-muted" });
    const actions = this.body.createDiv({ cls: "nuru-context-recovery-actions" });
    const check = actions.createEl("button", { text: "Check again" });
    const clear = actions.createEl("button", { text: "Clear and continue", cls: "mod-cta" });
    clear.disabled = true;
    checkbox.addEventListener("change", () => {
      this.restarted = checkbox.checked;
      clear.disabled = !stopped || !this.restarted || this.working || this.busy() || !/^[0-9a-f]{32}$/.test(info.run_id || "");
    });
    check.addEventListener("click", () => { if (!this.working) this.load(); });
    clear.addEventListener("click", async () => {
      if (clear.disabled || this.working || this.closed || !this.current() || !this.restarted) return;
      this.working = true; clear.disabled = true; check.disabled = true; checkbox.disabled = true;
      this.note.setText("Checking and clearing the reservation…");
      try {
        await this.client.recoverContext(info.run_id);
        if (!this.closed && this.current()) await this.load();
      } catch (error) {
        if (!this.closed && this.current()) this.note.setText(`${error.message} Check again before retrying.`);
      } finally {
        this.working = false;
        if (!this.closed && this.current()) {
          check.disabled = false;
          if (!this.recoveryReserved && this.info) this.render();
        }
      }
    });
    this.body.createEl("p", { text: `Reservation ${info.run_id || "unknown"} · Same steps: nuru-serve compute check`, cls: "nuru-context-muted" });
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
      this.body.createEl("p", { text: "Measure memory use before changing the size. Nuru briefly loads the model at a few bounded sizes, then puts it back. This may take a few minutes; chat waits. It cannot run while a reply is in progress." });
      this.body.createEl("p", { text: "Only when you press it. Never in the background.", cls: "nuru-context-muted" });
    }
    if (info.reason) this.body.createEl("p", { text: info.reason });
    this.body.createEl("p", { text: `Applies to every ${this.label} conversation`, cls: "nuru-context-muted" });
    this.action = this.body.createEl("button", { text: this.working ? (this.operation === "measure" ? "Measuring…" : "Reloading the model…") : info.measured ? "Apply" : "Measure memory use", cls: "mod-cta" });
    this.action.disabled = disabled || (info.measured ? this.pending === info.gb && !info.needs_apply : !info.can_measure);
    this.action.addEventListener("click", () => this.run(info.measured ? "apply" : "measure"));
    if (disabled && !this.working && !this.failed) this.note.setText("Nuru is busy. Wait until its replies and title work finish.");
  }
  async run(operation) {
    if (this.closed || this.working || this.failed || !this.current()) return;
    if (this.busy() || this.info.busy) { this.note.setText("Nuru is busy. Wait until its replies and title work finish."); return; }
    if (operation === "measure" && !this.info.can_measure) return;
    if (operation === "apply" && this.pending === this.info.gb && !this.info.needs_apply) return;
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
module.exports = { ContextPanel, checked, checkedRecovery };
