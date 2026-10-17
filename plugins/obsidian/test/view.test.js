"use strict";

const { FIXTURES, until } = require("./helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");
const { Component, MarkdownView, Menu, Notice, window } = require("obsidian");

const { Display } = require("../lib/protocol");
const { ConnectionProblem } = require("../lib/connection");
const { ChatView } = require("../lib/view");
const { renderReply } = require("../lib/render");

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
    chat: "ready", // what /v1/status says about the model
    async status() { calls.push(["status"]); return { ready: { chat: this.chat } }; },
    async conversations() { return { conversations: [{ id: "conv0000demo", title: "Trip", created_at: "2026-10-09T12:00:00.000Z" }] }; },
    saved: {}, // the conversation's stored model / thinking
    async conversation(id) { calls.push(["conversation", id]); return { conversation_id: id, turns: history, ...this.saved }; },
    async createConversation(title) { calls.push(["create", title]); return { conversation_id: "conv0000new1" }; },
    async submit(id, text, attachments, requestId, choice) {
      calls.push(["submit", id, text, attachments]);
      if (choice && Object.keys(choice).length) calls.push(["choice", choice]);
      return { turn_id: "turn0000new1" };
    },
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

async function open({ client, conversationId = "conv0000demo", connectError, file, selection = "", status } = {}) {
  const opened = [];
  // The note being edited; `editing.active = false` is the panel having the focus instead.
  const editing = { selection, active: true };
  const note = Object.assign(new MarkdownView(), { file, containerEl: document.createElement("div"),
    editor: { getSelection: () => editing.selection } });
  const app = { workspace: {
    getActiveFile: () => file, getActiveViewOfType: () => (editing.active ? note : null),
    on: () => ({ off() {} }),
    openLinkText: (...args) => opened.push(args), getLeavesOfType: () => [],
  } };
  const plugin = {
    settings: { port: 1, conversationId },
    vaultPath: () => "/vault",
    remember: async (changes) => Object.assign(plugin.settings, changes),
    connect: async () => { if (connectError) throw connectError; return { client, status }; },
  };
  const view = new ChatView({ app }, plugin);
  await view.open();
  return { view, opened, plugin, editing, note };
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
  assert.equal(view.sendButton.dataset.icon, "square", "the send arrow is a stop square while Nuru answers");
  assert.ok(view.sendButton.classList.contains("nuru-stop"));
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

test("a selection is shown, sent once, and shown again only when it changes", async () => {
  const client = fakeClient();
  const { view, editing } = await open({ client, file: { path: "nuru/Trip.md" }, selection: "Day 1\nDay 2\nDay 3" });
  document.dispatchEvent(new window.Event("selectionchange"));
  assert.equal(view.selectionEl.textContent, "Trip · 3 lines selected");
  view.input.value = "plan this";
  view.sendButton.click();
  await until(() => client.calls.some((c) => c[0] === "submit"));
  assert.deepEqual(client.calls.find((c) => c[0] === "submit")[3], [{ path: "nuru/Trip.md", selection: "Day 1\nDay 2\nDay 3" }]);
  await until(() => view.selectionEl.textContent === "");
  document.dispatchEvent(new window.Event("selectionchange"));
  assert.equal(view.selectionEl.textContent, "", "the same selection isn't offered again");
  editing.selection = "Day 2";
  document.dispatchEvent(new window.Event("selectionchange"));
  assert.equal(view.selectionEl.textContent, "Trip · 5 characters selected");
  editing.selection = "";
  document.dispatchEvent(new window.Event("selectionchange"));
  assert.equal(view.selectionEl.textContent, "");
  view.stop();
  await view.close();
});

test("selected table cells are sent as a Markdown table, with their columns' header", async () => {
  const client = fakeClient();
  // Obsidian's Live Preview table: the selected cells are marked; the editor itself holds a stray character.
  const { view, note } = await open({ client, file: { path: "Nuru/Learning/Model Benchmarks.md" }, selection: "x" });
  note.containerEl.innerHTML = `<table class="table-editor"><thead><tr><th>Feature</th><th>Gemma4:12b</th><th>Qwen3:8b</th></tr></thead>
    <tbody><tr><td class="is-selected start top">Tool discovery</td><td class="is-selected end top">5</td><td>5</td></tr>
    <tr><td class="is-selected start bottom">Path | file parsing</td><td class="is-selected end bottom">3</td><td>5</td></tr>
    <tr><td>Average</td><td>4.4</td><td>5</td></tr></tbody></table>`;
  document.dispatchEvent(new window.Event("mouseup"));
  assert.equal(view.selectionEl.textContent, "Model Benchmarks · table (2 rows × 2 columns) selected");
  view.input.value = "compare";
  view.sendButton.click();
  await until(() => client.calls.some((c) => c[0] === "submit"));
  assert.deepEqual(client.calls.find((c) => c[0] === "submit")[3], [{ path: "Nuru/Learning/Model Benchmarks.md",
    selection: "| Feature | Gemma4:12b |\n| --- | --- |\n| Tool discovery | 5 |\n| Path \\| file parsing | 3 |" }]);
  view.stop();
  await view.close();
});

test("an attached note isn't sent twice when text in it is also selected; the clip attaches the note", async () => {
  const client = fakeClient();
  const { view } = await open({ client, file: { path: "nuru/Trip.md" }, selection: "Rome" });
  assert.equal(view.containerEl.querySelector(".nuru-clip").dataset.icon, "paperclip");
  assert.equal(view.sendButton.dataset.icon, "arrow-up");
  document.dispatchEvent(new window.Event("selectionchange"));
  view.containerEl.querySelector(".nuru-clip").click();
  view.input.value = "summarise";
  view.sendButton.click();
  await until(() => client.calls.some((c) => c[0] === "submit"));
  assert.deepEqual(client.calls.find((c) => c[0] === "submit")[3], [{ path: "nuru/Trip.md" }]);
  view.stop();
  await view.close();
});

test("a message goes with only what was attached, and starts a conversation if none is open", async () => {
  const client = fakeClient();
  const { view, editing } = await open({ client, conversationId: null, file: { path: "nuru/Trip.md" }, selection: "Rome" });
  document.dispatchEvent(new window.Event("selectionchange"));
  assert.equal(view.selectionEl.textContent, "Trip · 4 characters selected");
  editing.active = false; // clicking into the panel: the note keeps its selection
  document.dispatchEvent(new window.Event("selectionchange"));
  assert.equal(view.selectionEl.textContent, "Trip · 4 characters selected");
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

test("a started turn with nothing to show says Nuru is getting ready while the model loads, then that it's thinking", async () => {
  const events = FIXTURES.streams.completed_with_tools;
  const started = events.findIndex((e) => e.type === "started") + 1;
  const history = [{ turn_id: "turn00000001", text: "trip?", state: "running", display: displayOf(events.slice(0, 1)) }];
  const client = fakeClient({ history, live: { turn00000001: events.slice(0, started) } });
  client.chat = "loading";
  const { view } = await open({ client, status: { ready: { chat: "idle" } } });
  const [turn] = view.turns.values();
  await until(() => turn.runner.display.state === "running");
  assert.match(turn.stateEl.textContent, /getting ready: loading its model and tools/);

  turn.waitingSince -= 5000; // as if it had waited five seconds
  view.tick();
  assert.match(turn.stateEl.textContent, /getting ready.*\(5 s\)/);
  await until(() => client.calls.some((c) => c[0] === "status"));

  client.chat = "ready";
  view.tick();
  await until(() => turn.stateEl.textContent.startsWith("Thinking…"));
  assert.match(turn.stateEl.textContent, /^Thinking… \(\d+ s\)$/);
  await view.close();
});

test("once the reply starts, the line says Nuru is writing it; the status is then checked only every 10 s", async () => {
  const events = FIXTURES.streams.completed_with_tools;
  const firstText = events.findIndex((e) => e.type === "text") + 1;
  const history = [{ turn_id: "turn00000001", text: "trip?", state: "running", display: displayOf(events.slice(0, 1)) }];
  const client = fakeClient({ history, live: { turn00000001: events.slice(0, firstText) } });
  const { view } = await open({ client, status: { ready: { chat: "ready" } } });
  const [turn] = view.turns.values();
  await until(() => turn.runner.display.reply);
  view.tick();
  assert.equal(turn.stateEl.textContent, "Writing the reply… · 1 tool call so far");
  assert.ok(!client.calls.some((c) => c[0] === "status"));
  for (let i = 0; i < 9; i += 1) view.tick();
  await until(() => client.calls.some((c) => c[0] === "status"));
  await view.close();
});

test("without a service the panel says why", async () => {
  const { view } = await open({ client: null, connectError: new ConnectionProblem("no-service") });
  assert.match(view.statusEl.textContent, /isn't running/);
  assert.ok(view.statusEl.classList.contains("nuru-error"));
  await view.close();
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test("pressing Enter twice while a message is on its way sends it once", async () => {
  const client = fakeClient();
  const answer = deferred();
  client.submit = async (...args) => { client.calls.push(["submit", ...args]); return answer.promise; };
  const { view } = await open({ client });
  view.input.value = "append this to my log";
  for (let i = 0; i < 2; i += 1) view.input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  view.answerAsk(["Yes"]);  // a question card's answer takes the same road
  answer.resolve({ turn_id: "turn0000new1" });
  await until(() => view.turns.has("turn0000new1"));
  assert.equal(client.calls.filter((c) => c[0] === "submit").length, 1);
  await view.close();
});

test("a message whose answer was lost is sent again under the same IDs, so it can't become two turns", async () => {
  const client = fakeClient();
  let lose = true;
  client.createConversation = async (title, id) => { client.calls.push(["create", id]); return { conversation_id: "conv0000new1" }; };
  client.submit = async (conversation, text, attachments, id) => {
    client.calls.push(["submit", id, text]);
    if (lose) throw Object.assign(new Error("lost"), { status: 0 });
    return { turn_id: "turn0000new1" };
  };
  const { view } = await open({ client, conversationId: null });
  view.input.value = "save the plan";
  await view.send();
  assert.equal(view.input.value, "save the plan", "kept, to send again");
  assert.match(Notice.shown.at(-1), /may have received this message/);
  lose = false;
  await view.send();
  const submits = client.calls.filter((c) => c[0] === "submit");
  assert.equal(submits.length, 2);
  assert.equal(submits[0][1], submits[1][1], "the same request ID both times");
  assert.equal(client.calls.filter((c) => c[0] === "create").length, 1, "the conversation was made once");
  assert.ok(view.turns.has("turn0000new1") && view.pending === null);
  await view.close();
});

test("after a lost answer, a changed message first finds out whether the old one arrived", async () => {
  const client = fakeClient();
  client.submit = async () => { throw Object.assign(new Error("lost"), { status: 0 }); };
  client.requestState = async (id, operation) => {
    client.calls.push(["state", id, operation]);
    return { conversation_id: "conv0000demo", turn_id: "turn0000old1", state: "running" };
  };
  const { view } = await open({ client });
  view.input.value = "first";
  await view.send();
  const lost = view.pending.submitId;
  view.input.value = "something else";
  await view.send();
  assert.deepEqual(client.calls.at(-1), ["state", lost, "submit_turn"]);
  assert.ok(view.turns.has("turn0000old1"), "the first message did arrive: it's shown");
  assert.equal(view.input.value, "something else", "the new text waits for the next Send");
  await view.close();
});

test("a slow history answer for a conversation left behind is dropped", async () => {
  const client = fakeClient();
  const slow = { conv0000aaaa: deferred(), conv0000bbbb: deferred() };
  client.conversation = (id) => slow[id].promise;
  const { view } = await open({ client, conversationId: null });
  const a = view.openConversation("conv0000aaaa");
  const b = view.openConversation("conv0000bbbb");
  slow.conv0000bbbb.resolve({ turns: [{ turn_id: "turn0000bbb1", text: "b", state: "completed", display: displayOf(FIXTURES.streams.failed) }] });
  await b;
  slow.conv0000aaaa.resolve({ turns: [{ turn_id: "turn0000aaa1", text: "a", state: "completed", display: displayOf(FIXTURES.streams.failed) }] });
  await a;
  assert.deepEqual([...view.turns.keys()], ["turn0000bbb1"]);
  assert.equal(view.conversationId, "conv0000bbbb");
  await view.close();
});

test("history that arrives after the panel closed starts nothing", async () => {
  const client = fakeClient();
  const slow = deferred();
  client.conversation = () => slow.promise;
  let streams = 0;
  const events = client.events.bind(client);
  client.events = (...args) => { streams += 1; return events(...args); };
  const { view } = await open({ client, conversationId: null });
  const opening = view.openConversation("conv0000demo");
  await view.close();
  slow.resolve({ turns: [{ turn_id: "turn00000001", text: "x", state: "running", display: displayOf(FIXTURES.streams.write_approved.slice(0, 2)) }] });
  await opening;
  assert.equal(streams, 0);
  assert.equal(view.turns.size, 0);
});

test("the reply shown is always the newest", async () => {
  const live = { turn00000001: FIXTURES.streams.completed_with_tools.slice(0, 9) };
  const history = [{ turn_id: "turn00000001", text: "x", state: "running", display: displayOf(live.turn00000001.slice(0, 1)) }];
  const { view } = await open({ client: fakeClient({ history, live }) });
  try {
    const [turn] = view.turns.values();
    await until(() => turn.renderedReply);
    for (const event of FIXTURES.streams.completed_with_tools.slice(9)) turn.runner.display.apply(event);
    view.update(turn);
    await until(() => turn.renderedReply === turn.runner.display.reply);
    const final = createDiv();
    renderReply(turn.runner.display.reply, final);
    assert.equal(turn.replyEl.innerHTML, final.innerHTML);
  } finally {
    await view.close();
  }
});

test("a late answer for a message sent from A leaves B's draft and attachments alone", async () => {
  const client = fakeClient({ history: [] });
  const answer = deferred();
  client.submit = async (...args) => { client.calls.push(["submit", ...args]); return answer.promise; };
  const { view } = await open({ client, conversationId: "conv0000aaaa", file: { path: "nuru/B.md" } });
  view.input.value = "for A";
  const sending = view.send();
  await view.openConversation("conv0000bbbb");
  view.input.value = "a draft for B";
  view.attach();
  answer.resolve({ turn_id: "turn0000aaa1" });
  await sending;
  assert.equal(view.input.value, "a draft for B");
  assert.deepEqual(view.attachments, [{ path: "nuru/B.md" }]);
  assert.equal(view.conversationId, "conv0000bbbb");
  assert.ok(!view.turns.has("turn0000aaa1"), "A's turn shows with A's history, not here");
  await view.close();
});

test("settling a lost message from A while in B never leaves B or mixes their turns", async () => {
  const histories = {
    conv0000aaaa: [],
    conv0000bbbb: [{ turn_id: "turn0000bbb1", text: "b", state: "completed", display: displayOf(FIXTURES.streams.failed) }],
  };
  const client = fakeClient();
  client.conversation = async (id) => ({ conversation_id: id, turns: histories[id] });
  let lose = true;
  client.submit = async (conversation, text, attachments, id) => {
    client.calls.push(["submit", conversation, text, id]);
    if (lose) throw Object.assign(new Error("lost"), { status: 0 });
    return { turn_id: "turn0000bbb2" };
  };
  client.requestState = async (id) => ({ conversation_id: "conv0000aaaa", turn_id: "turn0000aaa1", state: "running" });
  const { view } = await open({ client, conversationId: "conv0000aaaa" });
  view.input.value = "for A";
  await view.send();
  const lostId = view.pending.submitId;
  await view.openConversation("conv0000bbbb");
  view.input.value = "for A";  // the same words, but now in B: a new message, never A's retried
  lose = false;
  await view.send();
  assert.equal(view.conversationId, "conv0000bbbb");
  assert.deepEqual([...view.turns.keys()], ["turn0000bbb1", "turn0000bbb2"]);
  const [, last] = client.calls.filter((c) => c[0] === "submit");
  assert.equal(last[1], "conv0000bbbb");
  assert.notEqual(last[3], lostId);
  assert.match(Notice.shown.at(-1) || "", /did reach Nuru/);
  await view.close();
});

// A message lost in A, then a new one written in B whose Send must first find out about A's (held back).
async function pendingFromA(found) {
  const client = fakeClient();
  client.conversation = async (id) => ({ conversation_id: id, turns: [] });
  client.submit = async (conversation, text, attachments, id) => {
    client.calls.push(["submit", conversation, text, attachments, id]);
    throw Object.assign(new Error("lost"), { status: 0 });
  };
  const lookup = deferred();
  client.requestState = () => lookup.promise;
  const files = { path: "nuru/B.md" };
  const { view } = await open({ client, conversationId: "conv0000aaaa", file: files });
  view.input.value = "for A";
  await view.send();
  await view.openConversation("conv0000bbbb");
  view.input.value = "for B";
  view.attach();
  const sending = view.send();
  const release = () => (found ? lookup.resolve({ conversation_id: "conv0000aaaa", turn_id: "turn0000aaa1", state: "running" })
    : lookup.reject(Object.assign(new Error("not found"), { status: 404 })));
  return { view, client, sending, release, files };
}

for (const found of [true, false]) {
  test(`opening another conversation while a lost message is looked up sends nothing there (lookup ${found ? "found it" : "404"})`, async () => {
    const { view, client, sending, release, files } = await pendingFromA(found);
    await view.openConversation("conv0000cccc");
    files.path = "nuru/C.md";
    view.input.value = "a draft for C";
    view.attach();
    release();
    await sending;
    assert.equal(client.calls.filter((c) => c[0] === "submit").length, 1, "only A's first attempt was ever sent");
    assert.equal(view.conversationId, "conv0000cccc");
    assert.equal(view.input.value, "a draft for C");
    assert.deepEqual(view.attachments, [{ path: "nuru/B.md" }, { path: "nuru/C.md" }], "the composer is as it was left");
    assert.equal(view.turns.size, 0);
    await view.close();
  });
}

test("closing the panel while a lost message is looked up sends nothing", async () => {
  const { view, client, sending, release } = await pendingFromA(true);
  await view.close();
  release();
  await sending;
  assert.equal(client.calls.filter((c) => c[0] === "submit").length, 1);
});

test("once the lost message is settled, Send goes on with what was written, where it was written", async () => {
  const { view, client, sending, release } = await pendingFromA(false);
  release();
  await sending;
  const [, second] = client.calls.filter((c) => c[0] === "submit");
  assert.deepEqual(second.slice(1, 4), ["conv0000bbbb", "for B", [{ path: "nuru/B.md" }]]);
  await view.close();
});

// ------------------------------------------------------------------ activity (WP01-ACT)
function numbered(...pairs) {
  return pairs.map(([type, data], i) => ({ v: 1, conversation_id: "conv0000demo", turn_id: "turn00000001", seq: i + 1,
    at: "2026-10-11T12:00:00.000Z", type, data }));
}

test("a live turn shows its thinking folded, each call with what it was given and why it failed, and retries", async () => {
  const live = numbered(
    ["accepted", { request_id: "req-1" }], ["started", {}],
    ["reasoning", { text: "Read the daily note first.", final: false }],
    ["tool_start", { call_id: "call00000001", name: "read_note", arguments: { name: "nuru/daily-2026-10-11.md" } }],
    ["reasoning", { text: "Read the daily note first.", final: true }],
    ["tool_result", { call_id: "call00000001", ok: false, summary: "not_found" }],
    ["retry", { reason: "over budget" }],
    ["tool_start", { call_id: "call00000002", name: "write_note", arguments: { note: "nuru/daily-2026-10-11.md",
      content: "- [ ] Buy sugar", mode: "append" } }],
  );
  const history = [{ turn_id: "turn00000001", text: "fix it", state: "running", display: displayOf(live.slice(0, 1)) }];
  const { view } = await open({ client: fakeClient({ history, live: { turn00000001: live } }) });
  const [turn] = view.turns.values();
  await until(() => turn.runner.activity.steps.length === 4);
  const steps = [...turn.toolsEl.querySelectorAll(".nuru-step")];
  assert.deepEqual(steps.map((el) => (el.querySelector("summary") || el).textContent), [
    "Thought (5 words)",
    "✗ read_note nuru/daily-2026-10-11.md (not_found)",
    "↻ Thinking stopped (over budget): going on without it",
    "… write_note nuru/daily-2026-10-11.md",
  ]);
  const [thinking, failed, , writing] = steps;
  assert.equal(thinking.tagName, "DETAILS");
  assert.equal(thinking.open, false, "thinking starts folded");
  assert.equal(thinking.querySelector(".nuru-step-body").textContent, "Read the daily note first.");
  assert.ok(failed.classList.contains("nuru-failed"));
  assert.equal(failed.querySelector(".nuru-step-body").textContent, "name: nuru/daily-2026-10-11.md\nFailed: not_found");
  assert.equal(writing.querySelector(".nuru-step-body").textContent,
    "note: nuru/daily-2026-10-11.md\ncontent: - [ ] Buy sugar\nmode: append");
  view.tick();
  assert.match(turn.stateEl.textContent, /^Running write_note… · 2 tool calls so far$/);

  // A step the user opened stays open, and the same element, as the turn goes on.
  writing.open = true;
  turn.runner.display.apply({ ...live[0], seq: 9, type: "tool_result", data: { call_id: "call00000002", ok: true } });
  turn.runner.activity.apply({ ...live[0], seq: 9, type: "tool_result", data: { call_id: "call00000002", ok: true } });
  view.update(turn);
  assert.equal(turn.toolsEl.querySelectorAll(".nuru-step")[3], writing);
  assert.ok(writing.open);
  assert.equal(writing.querySelector("summary").textContent, "✓ write_note nuru/daily-2026-10-11.md");
  await view.close();
});

test("a finished turn from history shows its tools; Show all steps fetches the rest from its events", async () => {
  const stream = FIXTURES.streams.completed_with_tools;
  const history = [{ turn_id: "turn00000001", text: "trip?", state: "completed", display: displayOf(stream) }];
  const client = fakeClient({ history });
  const fetched = [];
  client.events = async (turnId, after, onEvent) => { fetched.push([turnId, after]); stream.forEach(onEvent); };
  const { view } = await open({ client });
  const [turn] = view.turns.values();
  assert.equal(turn.toolsEl.querySelector(".nuru-tool summary").textContent, "✓ search");
  assert.match(turn.toolsEl.querySelector(".nuru-tool .nuru-step-body").textContent, /not kept in history/);
  assert.deepEqual(fetched, [], "nothing is fetched until asked");
  turn.moreEl.click();
  await until(() => !turn.runner.activity.partial);
  assert.deepEqual(fetched, [["turn00000001", 0]]);
  assert.equal(turn.toolsEl.querySelector(".nuru-tool summary").textContent, "✓ search meeting notes Grüße (3 notes)");
  assert.equal(turn.moreEl, null);
  assert.equal(turn.toolsEl.querySelectorAll(".nuru-step").length, 2);
  await view.close();
});

test("Show all steps says so when the turn's events are no longer kept", async () => {
  const history = [{ turn_id: "turn00000001", text: "trip?", state: "completed",
    display: displayOf(FIXTURES.streams.completed_with_tools) }];
  const client = fakeClient({ history });
  client.events = async () => { throw Object.assign(new Error("expired"), { status: 410 }); };
  const { view } = await open({ client });
  const [turn] = view.turns.values();
  turn.moreEl.click();
  await until(() => turn.moreEl.textContent === "Steps no longer kept");
  assert.ok(turn.runner.activity.partial);
  await view.close();
});

test("the status line shows the vault, the model, the notes and the queue, and a problem in red", async () => {
  const status = { version: "0.1.0", vault: { id: "a", name: "ThrowAway" },
    ready: { chat: "ready", notes: "ready", queue: { active: 1, waiting: 2, limit: 8 } } };
  const client = fakeClient();
  const { view } = await open({ client, status });
  assert.equal(view.serviceEl.textContent, "ThrowAway · model ready · notes ready · 1 running, 2 waiting");
  assert.equal(view.serviceEl.getAttribute("title"), "nuru-serve 0.1.0");
  assert.ok(!view.serviceEl.classList.contains("nuru-error"));
  client.status = async () => ({ ...status, ready: { chat: "unavailable", notes: "ready", queue: { active: 0, waiting: 0, limit: 8 } } });
  for (let i = 0; i < 10; i += 1) view.tick();
  await until(() => view.serviceEl.textContent === "ThrowAway · model unavailable · notes ready");
  assert.ok(view.serviceEl.classList.contains("nuru-error"));
  await view.close();
});

test("the status line's problem colour wins over its faint colour", () => {
  const { JSDOM } = require("jsdom");
  const css = require("node:fs").readFileSync(require("node:path").join(__dirname, "..", "styles.css"), "utf8");
  const { window } = new JSDOM(`<style>${css}</style><div class="nuru-service nuru-error"></div><div class="nuru-service"></div>`);
  const [problem, fine] = window.document.querySelectorAll(".nuru-service");
  assert.equal(window.getComputedStyle(problem).color, "var(--text-error)");
  assert.equal(window.getComputedStyle(fine).color, "var(--text-faint)");
});

// The turn from 2026-10-11: the first write_note had no `note`, was refused before writing, and the
// second created the note. Only the second changed anything, and the panel must read that way.
function giuliaTurn() {
  const content = "Giulia Bona\n- Birthday: 20 September\n- Loves: Japanese food";
  const stream = numbered(
    ["accepted", { request_id: "req-1" }], ["started", {}],
    ["reasoning", { text: "Create a person note.", final: true }],
    ["tool_start", { call_id: "call00000001", name: "write_note", arguments: { content, type: "person" } }],
    ["tool_result", { call_id: "call00000001", ok: false, summary: "REJECTED_BEFORE_WRITE" }],
    ["write_receipt", { call_id: "call00000001", tool: "write_note", state: "failed", detail: "REJECTED_BEFORE_WRITE" }],
    ["reasoning", { text: "It needs a title.", final: true }],
    ["tool_start", { call_id: "call00000002", name: "write_note", arguments: { content, note: "Giulia Bona", type: "person" } }],
    ["tool_result", { call_id: "call00000002", ok: true }],
    ["write_receipt", { call_id: "call00000002", tool: "write_note", state: "applied", path: "nuru/giulia-bona.md" }],
    ["text", { text: "Created nuru/giulia-bona.md." }],
    ["completed", { reply: "Created nuru/giulia-bona.md." }],
  );
  stream[1].at = "2026-10-11T12:00:00.000Z";
  stream.at(-1).at = "2026-10-11T12:01:02.000Z";
  return stream;
}

test("a write refused before writing reads as not written, and each write says which call it was", async () => {
  const stream = giuliaTurn();
  const history = [{ turn_id: "turn00000001", text: "Add a note for Giulia", state: "running", display: displayOf(stream.slice(0, 1)) }];
  const { view, opened } = await open({ client: fakeClient({ history, live: { turn00000001: stream } }) });
  const [turn] = view.turns.values();
  await until(() => turn.runner.display.ended);
  const section = turn.toolsEl.querySelector("details.nuru-activity");
  assert.deepEqual([...section.querySelectorAll(".nuru-receipt")].map((el) => el.textContent), [
    "write_note, 1st call: not written (rejected before writing; nothing changed)",
    "write_note, 2nd call, nuru/giulia-bona.md: done",
  ], "what each write did is in the Thought section");
  assert.equal(turn.receiptsEl.textContent, "", "nothing else under the reply");
  assert.ok(!turn.vaultedEl.hidden && turn.vaultedEl.open, "Vaulted is shown, open");
  assert.equal(turn.vaultedEl.querySelector("summary").textContent, "Vaulted");
  assert.deepEqual([...turn.vaultedEl.querySelectorAll(".nuru-vaulted-notes a")].map((a) => a.textContent),
    ["nuru/giulia-bona.md"], "only the note that was written");
  const links = [...turn.el.querySelectorAll("a.internal-link")].filter((a) => a.textContent === "nuru/giulia-bona.md");
  assert.equal(links.length, 2, "the receipt's path and Vaulted both link to the note");
  for (const link of links) link.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
  assert.deepEqual(opened, [["nuru/giulia-bona.md", "", false], ["nuru/giulia-bona.md", "", false]]);
  assert.equal(section.querySelector(".nuru-receipt-failed a"), null, "a write that changed nothing links nowhere");
  const summaries = [...turn.toolsEl.querySelectorAll(".nuru-tool summary")].map((el) => el.textContent);
  assert.deepEqual(summaries, ["✗ write_note (REJECTED_BEFORE_WRITE)", "✓ write_note Giulia Bona"],
    "a call without a note isn't named by its content");
  await view.close();
});

test("all the steps sit in one section: open while the turn runs, folded with its time once it ends", async () => {
  const stream = giuliaTurn();
  const history = [{ turn_id: "turn00000001", text: "Add a note for Giulia", state: "running", display: displayOf(stream.slice(0, 1)) }];
  const client = fakeClient({ history, live: { turn00000001: stream.slice(0, 5) } });
  const { view } = await open({ client });
  const [turn] = view.turns.values();
  await until(() => turn.runner.activity.steps.length === 2);
  const section = turn.toolsEl.querySelector("details.nuru-activity");
  assert.equal(turn.toolsEl.children.length, 1, "one section holds every step");
  assert.equal(section.querySelectorAll(".nuru-step").length, 2);
  assert.ok(section.open);
  assert.equal(section.querySelector("summary").textContent, "Thinking…");
  for (const event of stream.slice(5)) {
    turn.runner.display.apply(event);
    turn.runner.activity.apply(event);
  }
  view.update(turn);
  assert.equal(section.querySelector("summary").textContent, "Thought (62 seconds)");
  assert.ok(!section.open, "folded once the turn ended");
  await view.close();
});

test("the section stays as the user left it", async () => {
  const stream = giuliaTurn();
  const history = [{ turn_id: "turn00000001", text: "Add a note for Giulia", state: "running", display: displayOf(stream.slice(0, 1)) }];
  const client = fakeClient({ history, live: { turn00000001: stream.slice(0, 5) } });
  const { view } = await open({ client });
  const [turn] = view.turns.values();
  await until(() => turn.runner.activity.steps.length === 2);
  const section = turn.toolsEl.querySelector("details.nuru-activity");
  await new Promise((resolve) => setTimeout(resolve, 20)); // the toggle from opening it has fired
  section.open = false; // the user folds it while the turn runs
  await until(() => turn.userToggled);
  for (const event of stream.slice(5)) {
    turn.runner.display.apply(event);
    turn.runner.activity.apply(event);
    view.update(turn);
  }
  assert.ok(!section.open);
  await view.close();
});

test("a finished turn from history shows its steps folded, counted rather than timed", async () => {
  const history = [{ turn_id: "turn00000001", text: "Add a note for Giulia", state: "completed", display: displayOf(giuliaTurn()) }];
  const { view } = await open({ client: fakeClient({ history }) });
  const [turn] = view.turns.values();
  const section = turn.toolsEl.querySelector("details.nuru-activity");
  assert.ok(!section.open);
  assert.equal(section.querySelector("summary").textContent, "Thought (3 steps)");
  assert.equal(section.querySelector(".nuru-receipt").textContent, "write_note, 1st call: not written (rejected before writing; nothing changed)");
  assert.equal(turn.vaultedEl.querySelector("a").getAttribute("data-href"), "nuru/giulia-bona.md");
  await view.close();
});

test("a write whose outcome is unknown stays under the reply too: that note needs checking", async () => {
  const stream = FIXTURES.streams.uncertain_write;
  const history = [{ turn_id: "turn00000001", text: "log it", state: "running", display: displayOf(stream.slice(0, 1)) }];
  const { view } = await open({ client: fakeClient({ history, live: { turn00000001: stream } }) });
  const [turn] = view.turns.values();
  await until(() => turn.runner.display.ended);
  assert.equal(turn.toolsEl.querySelector(".nuru-writes").textContent, "write_note nuru/Log.md: outcome unknown: check the note");
  assert.equal(turn.receiptsEl.textContent, "write_note nuru/Log.md: outcome unknown: check the note");
  assert.equal(turn.receiptsEl.querySelector("a"), null);
  await view.close();
});

test("Vaulted shows only once a note was written, and stays folded if the user folds it", async () => {
  const stream = giuliaTurn();
  const history = [{ turn_id: "turn00000001", text: "Add a note for Giulia", state: "running", display: displayOf(stream.slice(0, 1)) }];
  const { view } = await open({ client: fakeClient({ history, live: { turn00000001: stream.slice(0, 9) } }) });
  const [turn] = view.turns.values();
  await until(() => turn.runner.display.seq === 9);
  assert.ok(turn.vaultedEl.hidden, "nothing written yet");
  const event = stream[9]; // the second call's receipt: applied
  turn.runner.display.apply(event);
  turn.runner.activity.apply(event);
  view.update(turn);
  assert.ok(!turn.vaultedEl.hidden);
  turn.vaultedEl.open = false;
  for (const later of stream.slice(10)) {
    turn.runner.display.apply(later);
    turn.runner.activity.apply(later);
    view.update(turn);
  }
  assert.ok(!turn.vaultedEl.open);
  assert.equal(turn.vaultedEl.querySelectorAll("a").length, 1);
  await view.close();
});


const MODELS = { default: "qwen3.5:4b-q8_0", choices: [
  { id: "qwen3.5:4b-q8_0", label: "qwen3.5:4b-q8_0", cloud: false, default_thinking: "off",
    thinking: [{ id: "off", label: "Off" }, { id: "low", label: "On" }] },
  { id: "anthropic:claude-haiku-5-5", label: "Claude Haiku 5.5", cloud: true, default_thinking: "off",
    thinking: [{ id: "off", label: "Off" }, { id: "low", label: "Low" }, { id: "high", label: "High" }] },
  { id: "plain:1b", label: "plain:1b", cloud: false, default_thinking: null, thinking: [] },
] };

test("the model and thinking buttons show the conversation's choice; a pick goes with the next message", async () => {
  const client = fakeClient();
  const { view } = await open({ client, status: { ready: { chat: "ready" }, models: MODELS } });
  assert.equal(view.modelButton.textContent, "qwen3.5:4b-q8_0");
  assert.equal(view.thinkingButton.textContent, "Thinking: Off");

  view.modelButton.click();
  assert.deepEqual(Menu.shown.items.map((i) => [i.title, i.checked]),
    [["qwen3.5:4b-q8_0", true], ["Claude Haiku 5.5 ☁ cloud", false], ["plain:1b", false]]);
  Menu.shown.pick("Claude Haiku 5.5 ☁ cloud");
  assert.equal(view.modelButton.textContent, "Claude Haiku 5.5 ☁");
  view.thinkingButton.click();
  assert.deepEqual(Menu.shown.items.map((i) => i.title), ["Off", "Low", "High"]);
  Menu.shown.pick("High");
  assert.equal(view.thinkingButton.textContent, "Thinking: High");

  view.input.value = "hello";
  view.sendButton.click();
  await until(() => client.calls.some((c) => c[0] === "choice"));
  assert.deepEqual(client.calls.find((c) => c[0] === "choice")[1], { model: "anthropic:claude-haiku-5-5", thinking: "high" });
  await until(() => view.input.value === "");
  view.input.value = "again"; // the button is a stop button while the first turn runs: Enter sends
  view.input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await until(() => client.calls.filter((c) => c[0] === "submit").length === 2);
  assert.equal(client.calls.filter((c) => c[0] === "choice").length, 1, "the next message carries no change");
  view.stop();
  await view.close();
});

test("a model that can't think has no thinking button; an opened conversation shows its stored choice", async () => {
  const client = fakeClient();
  client.saved = { model: "anthropic:claude-haiku-5-5", thinking: "low" };
  const { view } = await open({ client, status: { ready: { chat: "ready" }, models: MODELS } });
  await until(() => view.modelButton.textContent === "Claude Haiku 5.5 ☁");
  assert.equal(view.thinkingButton.textContent, "Thinking: Low");
  view.modelButton.click();
  Menu.shown.pick("plain:1b");
  assert.ok(view.thinkingButton.hidden, "plain:1b can't think");
  await view.openConversation(null);
  assert.equal(view.modelButton.textContent, "qwen3.5:4b-q8_0", "a new conversation starts at the service's default");
  await view.close();
});

test("without a model list from the service, neither button shows", async () => {
  const { view } = await open({ client: fakeClient(), status: { ready: { chat: "ready" } } });
  assert.ok(view.modelButton.hidden && view.thinkingButton.hidden);
  await view.close();
});

test("a model picked while the last message is still on its way goes with the next one", async () => {
  const client = fakeClient();
  const answer = deferred();
  const submit = client.submit;
  let first = true; // only the first message's acceptance is slow
  client.submit = async (...args) => {
    const got = await submit.apply(client, args);
    if (first) { first = false; await answer.promise; }
    return got;
  };
  const { view } = await open({ client, status: { ready: { chat: "ready" }, models: MODELS } });
  view.modelButton.click();
  Menu.shown.pick("Claude Haiku 5.5 ☁ cloud");
  view.input.value = "hello";
  const sent = view.send();
  await until(() => client.calls.some((c) => c[0] === "choice"));
  view.modelButton.click();
  Menu.shown.pick("qwen3.5:4b-q8_0"); // back to local before Nuru has accepted the first message
  answer.resolve({ turn_id: "turn0000new1" });
  await sent;
  assert.equal(view.modelButton.textContent, "qwen3.5:4b-q8_0");
  view.input.value = "again";
  await view.send();
  assert.deepEqual(client.calls.filter((c) => c[0] === "choice").map((c) => c[1].model),
    ["anthropic:claude-haiku-5-5", "qwen3.5:4b-q8_0"], "the second message runs where the button says");
  view.stop();
  await view.close();
});

test("a selection that would be a sixth attachment isn't sent, and says so", async () => {
  const client = fakeClient();
  const { view } = await open({ client, file: { path: "nuru/Other.md" }, selection: "Rome" });
  view.attachments = ["A", "B", "C", "D", "E"].map((n) => ({ path: `nuru/${n}.md` }));
  view.renderChips();
  document.dispatchEvent(new window.Event("selectionchange"));
  assert.equal(view.selectionEl.textContent, "Other · 4 characters selected — not sent: 5 notes are attached, remove one to send it");
  view.input.value = "compare";
  await view.send();
  assert.equal(client.calls.find((c) => c[0] === "submit")[3].length, 5, "submit_turn takes at most 5");

  view.attachments = view.attachments.slice(1); // a note removed: the selection fits again
  view.renderChips();
  view.trackSelection();
  assert.equal(view.selectionEl.textContent, "Other · 4 characters selected");
  view.stop();
  await view.close();
});
