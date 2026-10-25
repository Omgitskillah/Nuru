// The Nuru sidebar: pick or start a conversation, send a message (with a note or selection attached
// only when asked), and watch the turn: the reply as Markdown, what Nuru did on the way (its thinking,
// each tool call with its arguments and why one failed, retries), a live line saying what it's doing,
// what each write actually did, and questions to answer before they expire. A status line under the
// header shows the service's /v1/status. Closing the panel stops following turns, never the
// turns themselves, and never answers a question.
"use strict";

const { ItemView, Keymap, MarkdownView, Menu, Notice, setIcon } = require("obsidian");

const { Activity, argumentLines, mainArgument, retryWords } = require("./activity");
const { LANTERN } = require("./icon");
const { answerText, newRequestId } = require("./protocol");
const { ConnectionProblem } = require("./connection");
const { LaunchProblem } = require("./launcher");
const { renderReply } = require("./render");
const { TurnRunner } = require("./turns");

const VIEW_TYPE = "nuru-chat";
const RENDER_MS = 80; // a streaming reply is rendered again at most this often
const SELECTION_CHARS = 16000; // a selection is sent up to this long
const MAX_ATTACHMENTS = 5; // what submit_turn takes, the selection included
const STATUS_SECONDS = 10; // the status line is refreshed this often while the panel is open
const CHAT_WORDS = { idle: "model not loaded yet", loading: "loading the model", ready: "model ready",
  unavailable: "model unavailable" };
const RECEIPT_WORDS = { applied: "done", refused: "not run", failed: "failed", uncertain: "outcome unknown: check the note" };
// A write the notes server refused before touching anything: it failed, but nothing changed.
const NOT_WRITTEN = "not written (rejected before writing; nothing changed)";
const STATE_WORDS = { accepted: "Sent", queued: "Waiting for Nuru", running: "", completed: "", failed: "Failed",
  cancelled: "Stopped" };

class ChatView extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.client = null;
    this.chat = null; // the service's chat readiness from /v1/status: idle, loading, ready or unavailable
    this.service = null; // the last /v1/status, for the status line
    this.statusCheck = null; // a /v1/status call on its way
    this.ticks = 0;
    this.conversationId = null;
    this.turns = new Map(); // turn ID -> {runner, text, el and its parts, renderTimer...}
    this.attachments = [];
    // Bumped by every navigation and by closing: an answer that arrives for an older one is dropped,
    // so a slow reply can't mix conversations or start a stream after the panel has closed.
    this.generation = 0;
    this.sending = false; // a message is on its way: another can't start
    // A message whose fate is unknown (its answer was lost): {text, attachments, origin, conversationId,
    // createId, submitId}. `origin` is the conversation it was written in (null: a new one). Sending the
    // same text there again reuses its IDs, so the service replays it instead of starting a second turn.
    this.pending = null;
  }

  getViewType() { return VIEW_TYPE; }

  getDisplayText() { return "Nuru"; }

  getIcon() { return LANTERN; }

  async onOpen() {
    const root = this.contentEl;
    root.empty();
    root.addClass("nuru-chat");
    const header = root.createDiv({ cls: "nuru-header" });
    this.picker = header.createEl("select", { cls: "nuru-picker", attr: { "aria-label": "Conversation" } });
    this.registerDomEvent(this.picker, "change", () => this.openConversation(this.picker.value || null));
    const fresh = header.createEl("button", { text: "New", attr: { "aria-label": "New conversation" } });
    this.registerDomEvent(fresh, "click", () => this.openConversation(null));
    this.serviceEl = root.createDiv({ cls: "nuru-service" });
    this.statusEl = root.createDiv({ cls: "nuru-status", attr: { role: "status" } });
    this.messagesEl = root.createDiv({ cls: "nuru-messages", attr: { role: "log", "aria-live": "polite" } });
    this.registerDomEvent(this.messagesEl, "click", (event) => this.onLinkClick(event));

    const composer = root.createDiv({ cls: "nuru-composer" });
    // What's selected in the open note goes with the next message; this line says so.
    this.selectionEl = composer.createDiv({ cls: "nuru-selection" });
    this.selection = null;
    this.sentSelection = null;
    // Table cells selected in Live Preview change no text selection: they're marked in the page.
    for (const type of ["selectionchange", "mouseup", "keyup"]) this.registerDomEvent(document, type, () => this.trackSelection());
    this.registerEvent(this.app.workspace.on("active-leaf-change", () => this.trackSelection()));
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
    const attachNote = buttons.createEl("button", { cls: "clickable-icon nuru-clip", attr: { "aria-label": "Attach note" } });
    setIcon(attachNote, "paperclip");
    this.registerDomEvent(attachNote, "click", () => this.attach());
    const actions = buttons.createDiv({ cls: "nuru-actions" });
    // The conversation's model and thinking level, each a small button with a menu; a change goes with
    // the next message. Hidden when the service offers no choice, and thinking for a model that can't.
    this.modelButton = actions.createEl("button", { cls: "nuru-pill", attr: { "aria-label": "Model" } });
    this.registerDomEvent(this.modelButton, "click", (event) => this.pickModel(event));
    this.thinkingButton = actions.createEl("button", { cls: "nuru-pill", attr: { "aria-label": "Thinking" } });
    this.registerDomEvent(this.thinkingButton, "click", (event) => this.pickThinking(event));
    this.choice = { model: null, thinking: null }; // null: the conversation's (or the service's) default
    this.changed = {}; // what the user picked since the conversation's last message
    this.renderChoice();
    // One button: an arrow that sends, a square that stops while Nuru is answering.
    this.sendButton = actions.createEl("button", { cls: "nuru-send" });
    this.registerDomEvent(this.sendButton, "click", () => (this.running ? this.stop() : this.send()));
    this.updateButtons();

    this.registerInterval(window.setInterval(() => this.tick(), 1000)); // question countdowns, waiting turns
    await this.reconnect();
  }

  async onClose() {
    this.generation += 1;
    for (const turn of this.turns.values()) this.forget(turn);
    this.turns.clear();
  }

  // ------------------------------------------------------------------ connection and conversations
  // Connect, starting the service for this vault first if it isn't running and `start` says so (the
  // setting, or the Start button: `clicked`). By itself it starts only when the launcher says it may.
  async reconnect({ start = this.plugin.settings.autoStart !== false, clicked = false } = {}) {
    const generation = ++this.generation;
    this.client = null;
    this.setStatus("Connecting to Nuru…");
    let client, status;
    try {
      ({ client, status } = await this.plugin.connect());
    } catch (error) {
      if (generation !== this.generation) return;
      if (!(start && error instanceof ConnectionProblem && ["no-service", "no-token"].includes(error.kind))) {
        this.showProblem(error);
        return;
      }
      const { launcher, manifest } = this.plugin;
      if (!clicked && !launcher.mayStartByItself(manifest.version)) {
        this.showProblem(new LaunchProblem("held"));
        return;
      }
      this.setStatus("Starting Nuru…");
      try {
        ({ client, status } = await launcher.startMarked(manifest.version));
      } catch (failure) {
        if (generation === this.generation) this.showProblem(failure);
        return;
      }
    }
    if (generation !== this.generation) return;
    this.client = client;
    this.showService(status);
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
    this.choice = { model: null, thinking: null };
    this.changed = {};
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
        this.choice = { model: history.model ?? null, thinking: history.thinking ?? null };
        for (const saved of [...history.turns].reverse()) this.addTurn(saved.turn_id, saved.text, saved.display);
      }
    }
    this.picker.value = this.conversationId || "";
    this.renderChoice();
    await this.plugin.remember({ conversationId: this.conversationId });
    this.updateButtons();
  }

  // ------------------------------------------------------------------ sending
  // Send what's in the composer, once. While it's on its way nothing else is sent (Enter, the button and
  // answering a question all come here). If its answer is lost, it stays pending: sending the same text
  // in the same conversation again reuses its request IDs, so the service replays it rather than starting
  // a second turn. Anything else first finds out what became of it, without leaving this conversation.
  async send() {
    if (this.sending || !this.client) return;
    const text = this.input.value.trim();
    if (!text) return;
    this.sending = true;
    this.sendButton.disabled = true;
    const generation = this.generation;
    // Everything this message is, taken now, before anything is awaited: what's on screen may change.
    const selected = this.selectionSent() ? this.selection : null;
    const selection = selected ? [{ path: selected.path, selection: selected.selection }] : [];
    const changed = this.changed; // replaced, never changed in place, by a pick made while this is sent
    const draft = { text, attachments: [...this.attachments, ...selection], origin: this.conversationId, choice: { ...changed } };
    try {
      let message = this.pending;
      if (message && (message.text !== text || message.origin !== draft.origin)) {
        await this.settle(message, generation);
        message = null;
        // The panel moved on (or closed) while that was found out: send nothing, leave the composer be.
        if (generation !== this.generation) return;
      }
      if (!message) {
        message = { ...draft, conversationId: draft.origin, createId: newRequestId(), submitId: newRequestId() };
      }
      this.pending = message;
      if (!message.conversationId) {
        const created = await this.client.createConversation(text.slice(0, 60), message.createId);
        message.conversationId = created.conversation_id;
      }
      const accepted = await this.client.submit(message.conversationId, message.text, message.attachments, message.submitId,
        message.choice);
      this.pending = null;
      if (generation === this.generation && this.changed === changed) this.changed = {};
      // The composer is cleared only if it still holds this message, in the view it was sent from.
      if (generation === this.generation && this.input.value.trim() === message.text) {
        this.input.value = "";
        this.attachments = [];
        this.renderChips();
        if (selection.length) {
          this.sentSelection = selection[0];
          this.selection = null;
          this.renderSelection();
        }
      }
      await this.show(message, accepted.turn_id, generation);
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

  // Find out what became of a pending message, and forget it. If it reached Nuru, it's shown when its
  // conversation is the one open; otherwise a notice says so. It never changes the open conversation.
  async settle(message, generation) {
    let state;
    try {
      state = await this.client.requestState(message.submitId, "submit_turn");
    } catch (error) {
      if (error.status !== 404) throw error; // still unknown: it stays pending
      this.pending = null;
      return;
    }
    this.pending = null;
    if (generation === this.generation && state.conversation_id === this.conversationId) {
      if (!this.turns.has(state.turn_id)) this.addTurn(state.turn_id, message.text);
    } else {
      new Notice(`Nuru: your earlier message "${message.text.slice(0, 40)}" did reach Nuru; it's in the conversation it was sent to.`);
    }
  }

  // A turn just accepted: shown if the panel is still where the message was sent from. A message that
  // started a new conversation opens it, but only if the panel is still on "New conversation".
  async show(message, turnId, generation) {
    if (generation !== this.generation) return;
    if (this.conversationId === null && message.origin === null) {
      this.conversationId = message.conversationId;
      if (!(await this.loadConversations(generation))) return;
      this.picker.value = message.conversationId;
      await this.plugin.remember({ conversationId: message.conversationId });
      if (generation !== this.generation) return;
    }
    if (this.conversationId === message.conversationId && !this.turns.has(turnId)) this.addTurn(turnId, message.text);
  }

  // Only what the user asks for: the open note. A selection in it goes by itself (trackSelection).
  attach() {
    const file = this.app.workspace.getActiveFile();
    if (!file) {
      new Notice("Nuru: open a note first.");
      return;
    }
    this.attachments = [...this.attachments.filter((a) => a.path !== file.path), { path: file.path }].slice(-MAX_ATTACHMENTS);
    this.renderChips();
  }

  // The text selected in the note being edited, kept while the focus is here in the panel: clicking
  // the panel makes it the active view, and the note's editor still holds its selection. A selection
  // already sent with a message isn't sent again until it changes.
  trackSelection() {
    const view = this.app.workspace.getActiveViewOfType(MarkdownView);
    if (!view) return;
    const table = selectedCells(view.containerEl);
    const text = table ? table.markdown : view.editor.getSelection();
    this.selection = text && view.file ? { path: view.file.path, selection: text.slice(0, SELECTION_CHARS), length: text.length,
      table: table && { rows: table.rows, columns: table.columns } } : null;
    if (this.sentSelection && this.selection && this.sentSelection.path === this.selection.path
        && this.sentSelection.selection === this.selection.selection) this.selection = null;
    else this.sentSelection = null;
    this.renderSelection();
  }

  // Whether the selection goes with the next message: not when its note is attached whole, nor when
  // that would make one attachment too many.
  selectionSent() {
    const selected = this.selection;
    return Boolean(selected) && !this.attachments.some((a) => a.path === selected.path)
      && this.attachments.length < MAX_ATTACHMENTS;
  }

  renderSelection() {
    const full = this.selection && !this.selectionSent() && !this.attachments.some((a) => a.path === this.selection.path);
    this.selectionEl.setText(!this.selection ? ""
      : selectionWords(this.selection) + (full ? ` — not sent: ${MAX_ATTACHMENTS} notes are attached, remove one to send it` : ""));
  }

  renderChips() {
    this.renderSelection(); // a full set of notes leaves no room for the selection
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
      toolsEl: el.createDiv({ cls: "nuru-tools" }), // the activity: thinking, tool calls, retries
      shownWritten: null, stepEls: [], shownActivity: null, moreEl: null, activityEl: null, autoOpen: null, userToggled: false,
      replyEl: el.createDiv({ cls: "nuru-reply markdown-rendered" }),
      vaultedEl: el.createEl("details", { cls: "nuru-vaulted", attr: { open: "" } }), // the notes it wrote
      receiptsEl: el.createDiv({ cls: "nuru-receipts" }),
      questionEl: el.createDiv({ cls: "nuru-question" }),
      renderTimer: null, renderedReply: null, forgotten: false, shownQuestion: null,
    };
    turn.runner = new TurnRunner({ client: this.client, turnId, onChange: () => this.update(turn) });
    // A finished turn from history shows its saved display; its full steps are fetched when asked for. An
    // unfinished one is followed from its first event, so its steps are all there.
    if (saved && ["completed", "failed", "cancelled"].includes(saved.state)) {
      Object.assign(turn.runner.display, saved);
      turn.runner.activity = Activity.fromDisplay(saved);
    }
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
  }

  update(turn) {
    const display = turn.runner.display;
    const words = turn.runner.connection === "reconnecting" ? "Reconnecting…"
      : turn.runner.connection === "lost" ? "This turn can't be followed any more; reopen the conversation."
      : display.state === "queued" ? `Waiting for Nuru (place ${display.position ?? "?"})`
      : display.state === "failed" ? `Failed: ${display.message}`
      : this.following(turn) ? this.stepWords(turn)
      : STATE_WORDS[display.state] + (display.truncated ? " (reply too long: shown in part)" : "");
    turn.stateEl.setText(words);
    turn.stateEl.toggleClass("nuru-error", display.state === "failed" || turn.runner.connection === "lost");

    this.renderActivity(turn);
    // What each write did goes in the Thought section, after the steps. Under the reply: "Vaulted", links to
    // the notes that were written, and any write whose outcome is unknown, since that note needs checking.
    turn.receiptsEl.empty();
    turn.writesEl?.empty();
    for (const receipt of display.receipts) this.receiptElement(turn.writesEl || turn.receiptsEl, turn, receipt);
    const written = [...new Set(display.receipts.filter((r) => r.state === "applied" && r.path).map((r) => r.path))];
    turn.vaultedEl.hidden = !written.length;
    if (written.join("\n") !== turn.shownWritten) { // rebuilt only when it changes: the user may have folded it
      turn.shownWritten = written.join("\n");
      turn.vaultedEl.empty();
      turn.vaultedEl.createEl("summary", { text: "Vaulted" });
      const list = turn.vaultedEl.createDiv({ cls: "nuru-vaulted-notes" });
      for (const path of written) noteLink(list.createDiv(), path);
    }
    if (turn.writesEl) {
      for (const receipt of display.receipts) if (receipt.state === "uncertain") this.receiptElement(turn.receiptsEl, turn, receipt);
    }
    for (const decision of display.decisions) {
      turn.receiptsEl.createDiv({ cls: "nuru-decision", text: `Answered: ${decision.decision}` +
        (decision.outcome === "answered" ? "" : ` (${decision.outcome === "expired" ? "no answer in time" : "stopped"})`) });
    }
    this.renderQuestion(turn);
    this.scheduleRender(turn);
    this.updateButtons();
  }

  // The reply is rendered again at most every RENDER_MS while it streams. Rendering is synchronous
  // (lib/render.js), so the newest reply is always the one shown.
  scheduleRender(turn) {
    if (turn.renderTimer) return;
    turn.renderTimer = window.setTimeout(() => this.render(turn), RENDER_MS);
  }

  render(turn) {
    turn.renderTimer = null;
    const reply = turn.runner.display.reply;
    if (reply === turn.renderedReply || turn.forgotten) return;
    const stick = this.nearBottom();
    renderReply(reply, turn.replyEl);
    turn.renderedReply = reply;
    if (stick) this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
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

  // ------------------------------------------------------------------ activity
  // The turn's steps, in order. Each step's element is kept and updated in place, so a thinking block
  // or a call's details the user opened stay open while the turn goes on.
  // All of them sit in one section, "Thought (N seconds)", open while the turn runs and folded once it
  // ends, unless the user opened or folded it.
  renderActivity(turn) {
    const { activity, display } = turn.runner;
    if (!activity.steps.length) return;
    if (!turn.activityEl) {
      turn.activityEl = turn.toolsEl.createEl("details", { cls: "nuru-activity" });
      turn.activitySummaryEl = turn.activityEl.createEl("summary");
      turn.stepsEl = turn.activityEl.createDiv({ cls: "nuru-steps" });
      turn.writesEl = turn.activityEl.createDiv({ cls: "nuru-writes" });
      this.registerDomEvent(turn.activityEl, "toggle", () => {
        if (turn.activityEl.open !== turn.autoOpen) turn.userToggled = true;
      });
    }
    const open = !display.ended && !activity.partial;
    if (!turn.userToggled && turn.activityEl.open !== open) {
      turn.autoOpen = open;
      turn.activityEl.open = open;
    }
    const seconds = activity.seconds();
    const steps = activity.steps.length;
    turn.activitySummaryEl.setText(!display.ended ? "Thinking…"
      : seconds !== null ? `Thought (${seconds} second${seconds === 1 ? "" : "s"})`
      : `Thought (${steps} step${steps === 1 ? "" : "s"})`);
    if (turn.shownActivity !== activity) { // the full steps replaced a history turn's summary
      for (const el of turn.stepEls.splice(0)) el.remove();
      turn.shownActivity = activity;
    }
    activity.steps.forEach((step, i) => {
      if (!turn.stepEls[i]) {
        turn.stepEls[i] = this.stepElement(step);
        turn.stepsEl.insertBefore(turn.stepEls[i], turn.moreEl);
      }
      this.updateStep(turn.stepEls[i], step, display.ended);
    });
    // A turn from history knows only its tools and last thinking: its full steps can be fetched.
    if (activity.partial && !turn.moreEl) {
      turn.moreEl = turn.stepsEl.createEl("button", { text: "Show all steps", cls: "nuru-more" });
      this.registerDomEvent(turn.moreEl, "click", () => this.loadSteps(turn));
    } else if (!activity.partial && turn.moreEl) {
      turn.moreEl.remove();
      turn.moreEl = null;
    }
  }

  // What a write did. When a tool wrote more than once in the turn, each says which call it was, so a
  // failed first try isn't read as the outcome of the one that worked.
  // A note it wrote is a link to that note.
  receiptElement(parent, turn, receipt) {
    const { receipts } = turn.runner.display;
    const same = receipts.filter((r) => r.tool === receipt.tool);
    const calls = turn.runner.activity.steps.filter((s) => s.kind === "tool" && s.name === receipt.tool);
    const index = calls.findIndex((s) => s.call_id === receipt.call_id);
    const n = (index >= 0 ? index : same.indexOf(receipt)) + 1;
    const which = same.length > 1 ? `, ${ordinal(n)} call${receipt.path ? "," : ""}` : "";
    const words = receipt.state === "failed" && receipt.detail === "REJECTED_BEFORE_WRITE" ? NOT_WRITTEN
      : RECEIPT_WORDS[receipt.state] || receipt.state;
    const el = parent.createDiv({ cls: `nuru-receipt nuru-receipt-${receipt.state}` });
    el.appendText(`${receipt.tool}${which}`);
    if (receipt.path) {
      el.appendText(" ");
      if (receipt.state === "applied") noteLink(el, receipt.path);
      else el.appendText(receipt.path);
    }
    el.appendText(`: ${words}`);
    return el;
  }

  stepElement(step) {
    let el;
    if (step.kind === "retry") {
      el = createDiv({ cls: "nuru-step nuru-retry" });
    } else {
      el = createEl("details", { cls: `nuru-step nuru-${step.kind}` });
      el.summaryEl = el.createEl("summary");
      el.bodyEl = el.createDiv({ cls: "nuru-step-body" });
    }
    return el;
  }

  updateStep(el, step, ended) {
    if (step.kind === "retry") {
      el.setText(`↻ ${retryWords(step.reason)}`);
      return;
    }
    if (step.kind === "thinking") {
      const words = step.text.trim().split(/\s+/).filter(Boolean).length;
      el.summaryEl.setText(step.done || ended ? `Thought (${words} word${words === 1 ? "" : "s"})` : "Thinking…");
      if (el.bodyEl.textContent !== step.text) el.bodyEl.setText(step.text);
      return;
    }
    const mark = step.ok === true ? "✓" : step.ok === false ? "✗" : ended ? "–" : "…";
    const named = mainArgument(step.arguments);
    const why = step.summary ? ` (${step.summary})` : "";
    el.summaryEl.setText(`${mark} ${step.name}${named ? ` ${named}` : ""}${why}`);
    el.toggleClass("nuru-failed", step.ok === false);
    const lines = step.arguments ? argumentLines(step.arguments) : ["(arguments not kept in history: Show all steps)"];
    if (step.ok === false) lines.push(`Failed: ${step.summary || "no reason given"}`);
    else if (step.ok === null && ended) lines.push("No result: the turn ended first");
    const text = lines.join("\n") || "(no arguments)";
    if (el.bodyEl.textContent !== text) el.bodyEl.setText(text);
  }

  // A finished turn's full steps, from its events (kept 30 days after it ended).
  async loadSteps(turn) {
    if (!this.client || turn.loadingSteps) return;
    turn.loadingSteps = true;
    turn.moreEl?.setText("Loading steps…");
    const activity = new Activity();
    const controller = new AbortController();
    try {
      await this.client.events(turn.runner.turnId, 0, (event) => activity.apply(event), controller.signal);
      if (turn.forgotten) return;
      turn.runner.activity = activity;
      this.update(turn);
    } catch (error) {
      if (turn.forgotten) return;
      turn.moreEl?.setText([404, 410].includes(error.status) ? "Steps no longer kept" : "Couldn't load the steps: try again");
    } finally {
      turn.loadingSteps = false;
    }
  }

  // A started turn the panel is following, not stopped at a question.
  following(turn) {
    const { display, connection } = turn.runner;
    const live = display.state === "running" && !display.question && connection === "live";
    if (!live) turn.waitingSince = null;
    else turn.waitingSince ??= Date.now();
    return live;
  }

  // The live step line: what Nuru is doing now and for how long. Before anything has happened on the
  // first turn after the service starts, that's loading the model and tools.
  stepWords(turn) {
    const { activity, display } = turn.runner;
    const time = (since) => {
      const seconds = Math.max(0, Math.round((Date.now() - since) / 1000));
      return seconds >= 2 ? ` (${seconds} s)` : "";
    };
    const now = activity.current();
    if (!now && !display.reply) {
      if (["idle", "loading"].includes(this.chat)) {
        return `Nuru is getting ready: loading its model and tools${time(turn.waitingSince)}. Only the first message after it starts waits for this.`;
      }
      return `Thinking…${time(turn.waitingSince)}`;
    }
    const calls = activity.steps.filter((s) => s.kind === "tool").length;
    const count = calls ? ` · ${calls} tool call${calls === 1 ? "" : "s"} so far` : "";
    return `${now ? now.what : "Writing the reply"}…${now ? time(now.since) : ""}${count}`;
  }

  // ------------------------------------------------------------------ model and thinking
  // The model this conversation runs on, as status.models describes it; null without a list.
  currentModel() {
    const models = this.service?.models;
    if (!models) return null;
    const id = this.choice.model ?? models.default;
    return models.choices.find((c) => c.id === id) || null;
  }

  currentThinking(model = this.currentModel()) {
    if (!model || !model.thinking.length) return null;
    const id = this.choice.thinking ?? model.default_thinking;
    return model.thinking.find((level) => level.id === id) || model.thinking[0];
  }

  renderChoice() {
    if (!this.modelButton) return;
    const model = this.currentModel();
    const thinking = this.currentThinking(model);
    this.modelButton.hidden = !model;
    this.modelButton.setText(model ? `${model.label}${model.cloud ? " ☁" : ""}` : "");
    this.modelButton.setAttr("title", model?.cloud ? "Runs on Claude: what Nuru reads is sent to Anthropic" : "Runs on this Mac");
    this.thinkingButton.hidden = !thinking;
    this.thinkingButton.setText(thinking ? `Thinking: ${thinking.label}` : "");
  }

  pickModel(event) {
    const models = this.service?.models;
    if (!models) return;
    const current = this.currentModel();
    const menu = new Menu();
    for (const choice of models.choices) {
      menu.addItem((item) => item.setTitle(`${choice.label}${choice.cloud ? " ☁ cloud" : ""}`)
        .setChecked(choice.id === current?.id)
        .onClick(() => {
          if (choice.id === current?.id) return;
          this.choice = { model: choice.id, thinking: null }; // a new model starts at its own default level
          this.changed = { model: choice.id };
          this.renderChoice();
        }));
    }
    menu.showAtMouseEvent(event);
  }

  pickThinking(event) {
    const model = this.currentModel();
    const current = this.currentThinking(model);
    if (!current) return;
    const menu = new Menu();
    for (const level of model.thinking) {
      menu.addItem((item) => item.setTitle(level.label).setChecked(level.id === current.id).onClick(() => {
        this.choice = { ...this.choice, thinking: level.id };
        this.changed = { ...this.changed, model: model.id, thinking: level.id };
        this.renderChoice();
      }));
    }
    menu.showAtMouseEvent(event);
  }

  // ------------------------------------------------------------------ the service's status
  showService(status) {
    if (!status?.ready) return;
    this.service = status;
    this.renderChoice();
    this.chat = status.ready.chat;
    const { ready } = status;
    const parts = [status.vault?.name, CHAT_WORDS[ready.chat] || ready.chat,
      ready.notes === "ready" ? "notes ready" : "notes unavailable"];
    if (ready.queue && (ready.queue.active || ready.queue.waiting)) {
      parts.push(`${ready.queue.active} running, ${ready.queue.waiting} waiting`);
    }
    this.serviceEl.setText(parts.filter(Boolean).join(" · "));
    this.serviceEl.toggleClass("nuru-error", ready.chat === "unavailable" || ready.notes !== "ready");
    if (status.version) this.serviceEl.setAttr("title", `nuru-serve ${status.version}`);
  }

  // Refresh the status: while a turn shows nothing yet (to see the model load), and every STATUS_SECONDS.
  // One call at a time.
  checkStatus() {
    if (this.statusCheck || !this.client) return;
    const client = this.client;
    this.statusCheck = client.status()
      .then((status) => { if (client === this.client) this.showService(status); })
      .catch(() => {}) // the turn's own stream reports a lost service
      .finally(() => {
        this.statusCheck = null;
        for (const turn of this.turns.values()) if (this.following(turn)) turn.stateEl.setText(this.stepWords(turn));
      });
  }

  tick() {
    this.ticks += 1;
    const live = [...this.turns.values()].filter((turn) => this.following(turn));
    for (const turn of live) turn.stateEl.setText(this.stepWords(turn));
    const loading = live.some((turn) => !turn.runner.activity.steps.length && !turn.runner.display.reply);
    if (loading || this.ticks % STATUS_SECONDS === 0) this.checkStatus();
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
    if (!this.sendButton) return;
    this.running = [...this.turns.values()].some((turn) => !turn.runner.display.ended && turn.runner.connection !== "lost");
    setIcon(this.sendButton, this.running ? "square" : "arrow-up");
    this.sendButton.toggleClass("nuru-stop", this.running);
    this.sendButton.setAttr("aria-label", this.running ? "Stop Nuru" : "Send");
  }

  setStatus(text, problem = false) {
    this.statusEl.setText(text);
    this.statusEl.toggleClass("nuru-error", problem);
  }

  // Why Nuru can't chat here, with what can be done about it: start it, or switch it to this vault.
  showProblem(error) {
    const known = error instanceof ConnectionProblem || error instanceof LaunchProblem;
    this.setStatus(known ? error.message : `Couldn't connect: ${error.message}`, true);
    if (error.kind === "busy" && this.otherVault) {
      this.statusEl.setText(`Nuru is busy with a turn in "${this.otherVault}": try again when it finishes.`);
    }
    this.otherVault = error.kind === "wrong-vault" ? error.vaultName : error.kind === "busy" ? this.otherVault : null;
    const action = error.kind === "wrong-vault" || error.kind === "busy" ? ["Switch Nuru to this vault", () => this.switchHere()]
      : ["no-service", "no-token", "exited", "slow", "not-stopped", "held"].includes(error.kind)
        ? ["Start Nuru", () => this.reconnect({ start: true, clicked: true })]
        : null;
    if (!action) return;
    const button = this.statusEl.createEl("button", { cls: "nuru-status-action", text: action[0] });
    this.registerDomEvent(button, "click", action[1]);
  }

  async switchHere() {
    const generation = ++this.generation;
    this.setStatus("Switching Nuru to this vault…");
    try {
      await this.plugin.launcher.stopOther();
    } catch (error) {
      if (generation === this.generation) this.showProblem(error);
      return;
    }
    if (generation === this.generation) await this.reconnect({ start: true, clicked: true });
  }
}

// A link to a note in the vault, opened by onLinkClick like a link in a reply.
function noteLink(parent, path) {
  return parent.createEl("a", { cls: "internal-link", text: path, attr: { href: path, "data-href": path } });
}

// The cells selected in a Live Preview table (Obsidian marks them td/th.is-selected in table.table-editor),
// as a Markdown table, with the header of their columns when it isn't selected itself. Null when none are.
function selectedCells(root) {
  const table = root.querySelector("table.table-editor:has(.is-selected)");
  if (!table) return null;
  const cell = (el) => el.textContent.trim().replace(/\|/g, "\\|").replace(/\n/g, " ");
  const rows = [...table.querySelectorAll("tr")].map((tr) => [...tr.children].filter((c) => c.matches("td, th")));
  const picked = rows.map((cells) => cells.map((c, i) => [i, c]).filter(([, c]) => c.classList.contains("is-selected")))
    .filter((cells) => cells.length);
  const columns = [...new Set(picked.flat().map(([i]) => i))].sort((a, b) => a - b);
  const header = rows[0] || [];
  const headerPicked = header.some((c) => c.classList.contains("is-selected"));
  const line = (values) => `| ${values.join(" | ")} |`;
  const body = picked.map((cells) => line(columns.map((i) => { const hit = cells.find(([j]) => j === i); return hit ? cell(hit[1]) : ""; })));
  const top = headerPicked ? body.shift() : line(columns.map((i) => (header[i] ? cell(header[i]) : "")));
  const markdown = [top, line(columns.map(() => "---")), ...body].join("\n");
  return { markdown, rows: picked.length, columns: columns.length };
}

// "Trip · 42 characters selected", lines for a selection across several, or the size of a table selection.
function selectionWords({ path, selection, length, table }) {
  const name = path.split("/").pop().replace(/\.md$/, "");
  const lines = selection.split("\n").length;
  const amount = table ? `table (${table.rows} row${table.rows === 1 ? "" : "s"} × ${table.columns} column${table.columns === 1 ? "" : "s"})`
    : lines > 1 ? `${lines} lines` : `${length.toLocaleString("en")} character${length === 1 ? "" : "s"}`;
  const cut = !table && length > SELECTION_CHARS ? ` (the first ${SELECTION_CHARS.toLocaleString("en")} characters are sent)` : "";
  return `${name} · ${amount} selected${cut}`;
}

function ordinal(n) {
  const tens = n % 100;
  const suffix = tens >= 11 && tens <= 13 ? "th" : { 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th";
  return `${n}${suffix}`;
}

module.exports = { ChatView, VIEW_TYPE };
