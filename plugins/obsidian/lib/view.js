// The Nuru sidebar: pick or start a conversation, send a message (with a note or selection attached
// only when asked), and watch the turn: the reply as Markdown, tool progress, what each write actually
// did, and questions to answer before they expire. Closing the panel stops following turns, never the
// turns themselves, and never answers a question.
"use strict";

const { Component, ItemView, Keymap, MarkdownRenderer, MarkdownView, Notice } = require("obsidian");

const { answerText, newRequestId } = require("./protocol");
const { ConnectionProblem } = require("./connection");
const { prepare, sanitize } = require("./render");
const { TurnRunner } = require("./turns");

const VIEW_TYPE = "nuru-chat";
const RENDER_MS = 80; // a streaming reply is rendered again at most this often
const RECEIPT_WORDS = { applied: "done", refused: "not run", failed: "failed", uncertain: "outcome unknown: check the note" };
const STATE_WORDS = { accepted: "Sent", queued: "Waiting for Nuru", running: "", completed: "", failed: "Failed",
  cancelled: "Stopped" };

class ChatView extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.client = null;
    this.conversationId = null;
    this.turns = new Map(); // turn ID -> {runner, display, text, el, parts..., component, renderTimer}
    this.attachments = [];
    // Bumped by every navigation and by closing: an answer that arrives for an older one is dropped,
    // so a slow reply can't mix conversations or start a stream after the panel has closed.
    this.generation = 0;
    this.sending = false; // a message is on its way: another can't start
    // A message whose fate is unknown (its answer was lost): {text, attachments, createId, submitId}.
    // Sending again reuses its IDs, so the service replays it instead of starting a second turn.
    this.pending = null;
  }

  getViewType() { return VIEW_TYPE; }

  getDisplayText() { return "Nuru"; }

  getIcon() { return "message-circle"; }

  async onOpen() {
    const root = this.contentEl;
    root.empty();
    root.addClass("nuru-chat");
    const header = root.createDiv({ cls: "nuru-header" });
    this.picker = header.createEl("select", { cls: "nuru-picker", attr: { "aria-label": "Conversation" } });
    this.registerDomEvent(this.picker, "change", () => this.openConversation(this.picker.value || null));
    const fresh = header.createEl("button", { text: "New", attr: { "aria-label": "New conversation" } });
    this.registerDomEvent(fresh, "click", () => this.openConversation(null));
    this.statusEl = root.createDiv({ cls: "nuru-status", attr: { role: "status" } });
    this.messagesEl = root.createDiv({ cls: "nuru-messages", attr: { role: "log", "aria-live": "polite" } });
    this.registerDomEvent(this.messagesEl, "click", (event) => this.onLinkClick(event));

    const composer = root.createDiv({ cls: "nuru-composer" });
    this.chipsEl = composer.createDiv({ cls: "nuru-attachments" });
    this.input = composer.createEl("textarea", { cls: "nuru-input", attr: { rows: "3", "aria-label": "Message to Nuru",
      placeholder: "Message Nuru (Enter to send, Shift+Enter for a new line)" } });
    this.registerDomEvent(this.input, "keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        this.send();
      }
    });
    const buttons = composer.createDiv({ cls: "nuru-buttons" });
    const attachNote = buttons.createEl("button", { text: "Attach note" });
    this.registerDomEvent(attachNote, "click", () => this.attach(false));
    const attachSelection = buttons.createEl("button", { text: "Attach selection" });
    this.registerDomEvent(attachSelection, "click", () => this.attach(true));
    this.stopButton = buttons.createEl("button", { text: "Stop", cls: "nuru-stop", attr: { "aria-label": "Stop Nuru" } });
    this.stopButton.hidden = true;
    this.registerDomEvent(this.stopButton, "click", () => this.stop());
    this.sendButton = buttons.createEl("button", { text: "Send", cls: "mod-cta" });
    this.registerDomEvent(this.sendButton, "click", () => this.send());

    this.registerInterval(window.setInterval(() => this.tick(), 1000)); // question countdowns
    await this.reconnect();
  }

  async onClose() {
    this.generation += 1;
    for (const turn of this.turns.values()) this.forget(turn);
    this.turns.clear();
  }

  // ------------------------------------------------------------------ connection and conversations
  async reconnect() {
    const generation = ++this.generation;
    this.client = null;
    this.setStatus("Connecting to Nuru…");
    let client;
    try {
      ({ client } = await this.plugin.connect());
    } catch (error) {
      if (generation === this.generation) {
        this.setStatus(error instanceof ConnectionProblem ? error.message : `Couldn't connect: ${error.message}`, true);
      }
      return;
    }
    if (generation !== this.generation) return;
    this.client = client;
    this.setStatus("");
    if (await this.loadConversations(generation)) await this.openConversation(this.plugin.settings.conversationId);
  }

  // Whether the list was shown: false if a newer navigation (or closing) came first.
  async loadConversations(generation = this.generation) {
    const { conversations } = await this.client.conversations();
    if (generation !== this.generation) return false;
    this.picker.empty();
    this.picker.createEl("option", { text: "New conversation", value: "" });
    for (const conversation of conversations) {
      this.picker.createEl("option", { value: conversation.id,
        text: conversation.title || `Conversation of ${conversation.created_at.slice(0, 16).replace("T", " ")}` });
    }
    return true;
  }

  async openConversation(id) {
    const generation = ++this.generation;
    for (const turn of this.turns.values()) this.forget(turn);
    this.turns.clear();
    this.messagesEl.empty();
    this.conversationId = null;
    if (id) {
      let history;
      try {
        history = await this.client.conversation(id);
      } catch (error) {
        if (generation !== this.generation) return;
        if (error.status !== 404) this.setStatus(`Couldn't load the conversation: ${error.message}`, true);
        id = null;
      }
      if (generation !== this.generation) return; // another conversation was opened, or the panel closed
      if (history) {
        this.conversationId = id;
        for (const saved of [...history.turns].reverse()) this.addTurn(saved.turn_id, saved.text, saved.display);
      }
    }
    this.picker.value = this.conversationId || "";
    await this.plugin.remember({ conversationId: this.conversationId });
    this.updateButtons();
  }

  // ------------------------------------------------------------------ sending
  // Send what's in the composer, once. While it's on its way nothing else is sent (Enter, the button and
  // answering a question all come here). If its answer is lost, it stays pending: sending again reuses
  // its request IDs, so the service replays it rather than starting a second turn.
  async send() {
    if (this.sending || !this.client) return;
    const text = this.input.value.trim();
    if (!text) return;
    this.sending = true;
    this.sendButton.disabled = true;
    const generation = this.generation;
    try {
      let message = this.pending;
      if (message && message.text !== text) {
        if (await this.settled(message, generation)) return; // it went: the new text waits for the next Send
        message = null; // it never arrived, and the text has changed: drop it
      }
      if (!message) {
        message = { text, attachments: [...this.attachments], createId: newRequestId(), submitId: newRequestId(),
          conversationId: this.conversationId };
      }
      this.pending = message;
      if (!message.conversationId) {
        const created = await this.client.createConversation(text.slice(0, 60), message.createId);
        message.conversationId = created.conversation_id;
      }
      const accepted = await this.client.submit(message.conversationId, message.text, message.attachments, message.submitId);
      this.pending = null;
      this.input.value = "";
      this.attachments = [];
      this.renderChips();
      await this.show(message.conversationId, accepted.turn_id, message.text, generation);
    } catch (error) {
      if (error.status === 0 && this.pending) {
        new Notice("Nuru may have received this message: its answer was lost. Press Send again to find out; it won't be sent twice.");
      } else {
        this.pending = null;
        new Notice(`Nuru: ${error.message}`);
      }
    } finally {
      this.sending = false;
      this.sendButton.disabled = false;
      this.updateButtons();
    }
  }

  // Whether a pending message (one whose answer was lost) reached Nuru; if it did, it's shown.
  async settled(message, generation) {
    try {
      const state = await this.client.requestState(message.submitId, "submit_turn");
      this.pending = null;
      await this.show(state.conversation_id, state.turn_id, message.text, generation);
      return true;
    } catch (error) {
      if (error.status === 404) return false;
      throw error;
    }
  }

  // A turn just accepted: shown if its conversation is still the one open and the panel hasn't moved
  // on; otherwise it appears with that conversation's history.
  async show(conversationId, turnId, text, generation) {
    if (generation !== this.generation) return;
    if (this.conversationId !== conversationId) {
      this.conversationId = conversationId;
      await this.loadConversations(generation);
      if (generation !== this.generation) return;
      this.picker.value = conversationId;
      await this.plugin.remember({ conversationId });
      if (generation !== this.generation) return;
    }
    if (!this.turns.has(turnId)) this.addTurn(turnId, text);
  }

  // Only what the user asks for: the open note, or the text selected in it.
  attach(selection) {
    const file = this.app.workspace.getActiveFile();
    if (!file) {
      new Notice("Nuru: open a note first.");
      return;
    }
    const attachment = { path: file.path };
    if (selection) {
      const editor = this.app.workspace.getActiveViewOfType(MarkdownView)?.editor;
      const selected = editor ? editor.getSelection() : "";
      if (!selected) {
        new Notice("Nuru: select some text in the note first.");
        return;
      }
      attachment.selection = selected.slice(0, 16000);
    }
    this.attachments = [...this.attachments.filter((a) => a.path !== file.path), attachment].slice(-5);
    this.renderChips();
  }

  renderChips() {
    this.chipsEl.empty();
    for (const attachment of this.attachments) {
      const chip = this.chipsEl.createDiv({ cls: "nuru-chip" });
      chip.createSpan({ text: attachment.selection ? `${attachment.path} (selection)` : attachment.path });
      const remove = chip.createEl("button", { text: "×", attr: { "aria-label": `Remove ${attachment.path}` } });
      this.registerDomEvent(remove, "click", () => {
        this.attachments = this.attachments.filter((a) => a !== attachment);
        this.renderChips();
      });
    }
  }

  stop() {
    for (const turn of this.turns.values()) {
      if (!turn.runner.display.ended) {
        turn.runner.cancel().catch((error) => new Notice(`Nuru: ${error.message}`));
      }
    }
  }

  // ------------------------------------------------------------------ turns
  addTurn(turnId, text, saved) {
    const el = this.messagesEl.createDiv({ cls: "nuru-turn" });
    el.createDiv({ cls: "nuru-user", text });
    const turn = {
      text, el,
      stateEl: el.createDiv({ cls: "nuru-state" }),
      toolsEl: el.createDiv({ cls: "nuru-tools" }),
      replyEl: el.createDiv({ cls: "nuru-reply markdown-rendered" }),
      receiptsEl: el.createDiv({ cls: "nuru-receipts" }),
      questionEl: el.createDiv({ cls: "nuru-question" }),
      component: this.addChild(new Component()),
      renderTimer: null, renderedReply: null, rendering: false, renderComponent: null, forgotten: false, shownQuestion: null,
    };
    turn.runner = new TurnRunner({ client: this.client, turnId, onChange: () => this.update(turn) });
    if (saved) Object.assign(turn.runner.display, saved);
    this.turns.set(turnId, turn);
    for (const earlier of this.turns.values()) this.renderQuestion(earlier); // only the last turn's ask stays open
    this.update(turn);
    if (!turn.runner.display.ended) turn.runner.run().then(() => this.update(turn));
    return turn;
  }

  forget(turn) {
    turn.forgotten = true;
    turn.runner.stop();
    window.clearTimeout(turn.renderTimer);
    this.removeChild(turn.component);
  }

  update(turn) {
    const display = turn.runner.display;
    const words = turn.runner.connection === "reconnecting" ? "Reconnecting…"
      : turn.runner.connection === "lost" ? "This turn can't be followed any more; reopen the conversation."
      : display.state === "queued" ? `Waiting for Nuru (place ${display.position ?? "?"})`
      : display.state === "failed" ? `Failed: ${display.message}`
      : STATE_WORDS[display.state] + (display.truncated ? " (reply too long: shown in part)" : "");
    turn.stateEl.setText(words);
    turn.stateEl.toggleClass("nuru-error", display.state === "failed" || turn.runner.connection === "lost");

    turn.toolsEl.empty();
    for (const tool of display.tools) {
      const mark = tool.ok === true ? "✓" : tool.ok === false ? "✗" : display.ended ? "–" : "…";
      turn.toolsEl.createDiv({ cls: "nuru-tool", text: `${mark} ${tool.name}` });
    }
    turn.receiptsEl.empty();
    for (const receipt of display.receipts) {
      turn.receiptsEl.createDiv({ cls: `nuru-receipt nuru-receipt-${receipt.state}`,
        text: `${receipt.tool}${receipt.path ? ` ${receipt.path}` : ""}: ${RECEIPT_WORDS[receipt.state] || receipt.state}` });
    }
    for (const decision of display.decisions) {
      turn.receiptsEl.createDiv({ cls: "nuru-decision", text: `Answered: ${decision.decision}` +
        (decision.outcome === "answered" ? "" : ` (${decision.outcome === "expired" ? "no answer in time" : "stopped"})`) });
    }
    this.renderQuestion(turn);
    this.scheduleRender(turn);
    this.updateButtons();
  }

  // The reply is rendered again at most every RENDER_MS while it streams, one render at a time: Obsidian's
  // renderer can finish out of order, so a render that's running is let finish, then the newest reply
  // is rendered. Each render has its own component, released once its result is replaced.
  scheduleRender(turn) {
    if (turn.renderTimer || turn.rendering) return;
    turn.renderTimer = window.setTimeout(() => this.render(turn), RENDER_MS);
  }

  async render(turn) {
    turn.renderTimer = null;
    const reply = turn.runner.display.reply;
    if (reply === turn.renderedReply || turn.forgotten) return;
    turn.rendering = true;
    const component = turn.component.addChild(new Component());
    const fresh = createDiv();
    try {
      await MarkdownRenderer.render(this.app, prepare(reply), fresh, "", component);
    } finally {
      turn.rendering = false;
    }
    if (turn.forgotten) return;
    sanitize(fresh);
    const stick = this.nearBottom();
    turn.replyEl.empty();
    turn.replyEl.append(...fresh.childNodes);
    if (turn.renderComponent) turn.component.removeChild(turn.renderComponent);
    turn.renderComponent = component;
    turn.renderedReply = reply;
    if (stick) this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    if (turn.runner.display.reply !== reply) this.scheduleRender(turn); // it grew meanwhile
  }

  nearBottom() {
    const el = this.messagesEl;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  // A question Nuru is waiting on (with a countdown to when the service stops waiting), or the one the
  // model ended its reply with (answered by sending the answer as the next message).
  renderQuestion(turn) {
    const display = turn.runner.display;
    const open = display.question;
    const asked = !open && display.state === "completed" && display.ask && turn === [...this.turns.values()].at(-1)
      ? display.ask : null;
    const key = open ? open.question_id : asked ? "ask" : null;
    if (key === turn.shownQuestion) return;
    turn.shownQuestion = key;
    turn.questionEl.empty();
    if (!key) return;
    const question = open ? open.question : asked;
    const card = turn.questionEl.createDiv({ cls: "nuru-card", attr: { role: "group", "aria-label": "Nuru asks" } });
    card.createDiv({ cls: "nuru-card-text", text: question.text });
    if (question.details) card.createDiv({ cls: "nuru-card-details", text: question.details });
    if (open) card.createDiv({ cls: "nuru-countdown", attr: { "data-expires": open.expires_at } });
    const submit = (chosen, other) => (open ? this.answer(turn, open, chosen, other) : this.answerAsk(chosen, other));
    const multi = question.kind === "multi";
    const picked = new Set();
    for (const option of question.options) {
      const label = option.label + (option.recommended ? " (recommended)" : "");
      if (multi) {
        const row = card.createEl("label", { cls: "nuru-option" });
        const box = row.createEl("input", { attr: { type: "checkbox" } });
        row.appendText(` ${label}`);
        this.registerDomEvent(box, "change", () => (box.checked ? picked.add(option.label) : picked.delete(option.label)));
      } else {
        const button = card.createEl("button", { text: label, cls: option.recommended ? "mod-cta" : "" });
        if (option.description) button.setAttr("title", option.description);
        this.registerDomEvent(button, "click", () => submit([option.label]));
      }
    }
    let other = null;
    if (question.allow_other) {
      other = card.createEl("input", { attr: { type: "text", placeholder: "Or say what you'd like", "aria-label": "Your own answer" } });
      if (!multi) {
        const send = card.createEl("button", { text: "Send answer" });
        this.registerDomEvent(send, "click", () => other.value.trim() && submit([], other.value.trim()));
      }
    }
    if (multi) {
      const done = card.createEl("button", { text: "Answer", cls: "mod-cta" });
      this.registerDomEvent(done, "click", () => submit([...picked], other && other.value.trim() ? other.value.trim() : undefined));
    }
    this.tick();
  }

  async answer(turn, open, chosen, other) {
    for (const button of turn.questionEl.querySelectorAll("button")) button.disabled = true;
    try {
      await this.client.answer(open.question_id, open.operation, chosen, other);
    } catch (error) {
      new Notice(error.code === "question_closed" ? "Nuru: that question had already closed." : `Nuru: ${error.message}`);
      for (const button of turn.questionEl.querySelectorAll("button")) button.disabled = false;
    }
  }

  answerAsk(chosen, other) {
    if (this.sending) {
      new Notice("Nuru: wait for the last message to go first.");
      return;
    }
    this.input.value = answerText(chosen, other);
    this.send();
  }

  tick() {
    for (const el of this.contentEl.querySelectorAll(".nuru-countdown")) {
      const left = Math.max(0, Math.round((Date.parse(el.getAttribute("data-expires")) - Date.now()) / 1000));
      el.setText(left ? `${left} s to answer; no answer counts as No` : "Time's up: taken as No");
      if (!left) for (const button of el.parentElement.querySelectorAll("button")) button.disabled = true;
    }
  }

  // Notes open in Obsidian; web links in the browser; anything else does nothing (sanitize removed it).
  onLinkClick(event) {
    const link = event.target.closest("a");
    if (!link || !this.messagesEl.contains(link)) return;
    event.preventDefault();
    const note = link.classList.contains("internal-link") && link.getAttribute("data-href");
    if (note) {
      this.app.workspace.openLinkText(note, "", Keymap.isModEvent(event));
    } else if (link.getAttribute("href")) {
      window.open(link.getAttribute("href"), "_blank");
    }
  }

  updateButtons() {
    const running = [...this.turns.values()].some((turn) => !turn.runner.display.ended && turn.runner.connection !== "lost");
    if (this.stopButton) this.stopButton.hidden = !running;
  }

  setStatus(text, problem = false) {
    this.statusEl.setText(text);
    this.statusEl.toggleClass("nuru-error", problem);
  }
}

module.exports = { ChatView, VIEW_TYPE };
