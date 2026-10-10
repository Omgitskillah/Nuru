"use strict";

const { FIXTURES, until } = require("./helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");
const { Component, Notice, window } = require("obsidian");

const { Display } = require("../lib/protocol");
const { ConnectionProblem } = require("../lib/connection");
const { ChatView } = require("../lib/view");

function displayOf(events) {
  const display = new Display();
  for (const event of events) display.apply(event);
  return JSON.parse(JSON.stringify(display));
}

// A fake nuru-serve client. `live` turns stream their events and then wait, as an unfinished turn does.
function fakeClient({ history = [], live = {} } = {}) {
  const calls = [];
  return {
    calls,
    aborted: 0,
    async conversations() { return { conversations: [{ id: "conv0000demo", title: "Trip", created_at: "2026-10-09T12:00:00.000Z" }] }; },
    async conversation(id) { calls.push(["conversation", id]); return { conversation_id: id, turns: history }; },
    async createConversation(title) { calls.push(["create", title]); return { conversation_id: "conv0000new1" }; },
    async submit(id, text, attachments) { calls.push(["submit", id, text, attachments]); return { turn_id: "turn0000new1" }; },
    async cancel(turnId) { calls.push(["cancel", turnId]); return {}; },
    async answer(...args) { calls.push(["answer", ...args]); return {}; },
    events(turnId, after, onEvent, signal) {
      (live[turnId] || []).filter((e) => e.seq > after).forEach(onEvent);
      return new Promise((resolve, reject) => signal.addEventListener("abort", () => {
        this.aborted += 1;
        reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
      }));
    },
  };
}

async function open({ client, conversationId = "conv0000demo", connectError, file, selection = "" } = {}) {
  const opened = [];
  const app = { workspace: {
    getActiveFile: () => file, getActiveViewOfType: () => ({ editor: { getSelection: () => selection } }),
    openLinkText: (...args) => opened.push(args), getLeavesOfType: () => [],
  } };
  const plugin = {
    settings: { port: 1, conversationId },
    vaultPath: () => "/vault",
    remember: async (changes) => Object.assign(plugin.settings, changes),
    connect: async () => { if (connectError) throw connectError; return { client }; },
  };
  const view = new ChatView({ app }, plugin);
  await view.open();
  return { view, opened, plugin };
}

const rendered = (turn) => until(() => turn.replyEl.childNodes.length > 0 || turn.runner.display.reply === "");

test("history shows each turn's reply safely, its tools and what its writes did", async () => {
  const history = [
    { turn_id: "turn00000003", text: "log it", state: "failed", display: displayOf(FIXTURES.streams.uncertain_write) },
    { turn_id: "turn00000002", text: "evil", state: "completed", display: displayOf(FIXTURES.streams.hostile_markdown) },
    { turn_id: "turn00000001", text: "trip?", state: "completed", display: displayOf(FIXTURES.streams.completed_with_tools) },
  ];
  const { view, opened } = await open({ client: fakeClient({ history }) });
  const [first, second, third] = view.turns.values();
  assert.equal(first.el.querySelector(".nuru-user").textContent, "trip?", "oldest first");
  await rendered(first);
  await rendered(second);
  assert.equal(view.contentEl.querySelector(".nuru-reply script, .nuru-reply img, [onerror]"), null);
  assert.ok(second.replyEl.textContent.includes("<script>"));
  assert.ok(first.toolsEl.textContent.includes("✓"));
  const link = [...first.replyEl.querySelectorAll("a.internal-link")].find((a) => a.textContent === "🌍 Trip");
  link.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
  assert.deepEqual(opened, [["🌍 Trip", "", false]]);
  assert.equal(third.receiptsEl.textContent, "write_note nuru/Log.md: outcome unknown: check the note");
  assert.ok(third.receiptsEl.querySelector(".nuru-receipt-uncertain"));
  await view.close();
});

test("a question waiting for an answer gets a card; answering sends its operation", async () => {
  const events = FIXTURES.streams.write_approved.slice(0, 4);
  const question = events[3].data;
  const live = { turn00000001: events.map((e) => (e.type === "question"
    ? { ...e, data: { ...e.data, expires_at: new Date(Date.now() + 9000).toISOString() } } : e)) };
  const history = [{ turn_id: "turn00000001", text: "save it", state: "running", display: displayOf(events.slice(0, 1)) }];
  const client = fakeClient({ history, live });
  const { view } = await open({ client });
  const [turn] = view.turns.values();
  await until(() => turn.questionEl.querySelector("button"));
  assert.match(turn.questionEl.querySelector(".nuru-countdown").textContent, /s to answer/);
  assert.equal(view.stopButton.hidden, false);
  const yes = [...turn.questionEl.querySelectorAll("button")].find((b) => b.textContent.startsWith("Yes"));
  yes.click();
  await until(() => client.calls.some((c) => c[0] === "answer"));
  assert.deepEqual(client.calls.find((c) => c[0] === "answer"), ["answer", question.question_id, question.operation, ["Yes"], undefined]);

  const listeners = Component.listeners;
  await view.close();
  assert.equal(client.aborted, 1, "the stream stops; the turn isn't cancelled");
  assert.ok(!client.calls.some((c) => c[0] === "cancel"));
  assert.ok(Component.listeners < listeners && Component.intervals === 0);
});

test("a message goes with only what was attached, and starts a conversation if none is open", async () => {
  const client = fakeClient();
  const { view } = await open({ client, conversationId: null, file: { path: "nuru/Trip.md" }, selection: "Rome" });
  view.attach(true);
  view.input.value = "summarise this";
  view.input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await until(() => client.calls.some((c) => c[0] === "submit"));
  assert.deepEqual(client.calls.filter((c) => c[0] !== "conversation"), [
    ["create", "summarise this"],
    ["submit", "conv0000new1", "summarise this", [{ path: "nuru/Trip.md", selection: "Rome" }]],
  ]);
  assert.equal(view.input.value, "");
  assert.equal(view.chipsEl.childNodes.length, 0);
  view.stop();
  await until(() => client.calls.some((c) => c[0] === "cancel"));
  await view.close();
});

test("the model's own question is answered by sending the answer as the next message", async () => {
  const history = [{ turn_id: "turn00000001", text: "plan", state: "completed", display: displayOf(FIXTURES.streams.model_asks) }];
  const client = fakeClient({ history });
  const { view } = await open({ client });
  const [turn] = view.turns.values();
  const button = turn.questionEl.querySelector("button");
  const label = displayOf(FIXTURES.streams.model_asks).ask.options[0].label;
  button.click();
  await until(() => client.calls.some((c) => c[0] === "submit"));
  assert.equal(client.calls.find((c) => c[0] === "submit")[2], label);
  await view.close();
});

test("without a service the panel says why", async () => {
  const { view } = await open({ client: null, connectError: new ConnectionProblem("no-service") });
  assert.match(view.statusEl.textContent, /isn't running/);
  assert.ok(view.statusEl.classList.contains("nuru-error"));
  await view.close();
});
