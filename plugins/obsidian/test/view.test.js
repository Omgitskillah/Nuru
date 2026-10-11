"use strict";

const { FIXTURES, until } = require("./helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");
const { Component, Notice, window } = require("obsidian");

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

async function open({ client, conversationId = "conv0000demo", connectError, file, selection = "", status } = {}) {
  const opened = [];
  const app = { workspace: {
    getActiveFile: () => file, getActiveViewOfType: () => ({ editor: { getSelection: () => selection } }),
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

test("once the reply starts, the waiting line goes and no more status checks are made", async () => {
  const events = FIXTURES.streams.completed_with_tools;
  const firstText = events.findIndex((e) => e.type === "text") + 1;
  const history = [{ turn_id: "turn00000001", text: "trip?", state: "running", display: displayOf(events.slice(0, 1)) }];
  const client = fakeClient({ history, live: { turn00000001: events.slice(0, firstText) } });
  const { view } = await open({ client, status: { ready: { chat: "ready" } } });
  const [turn] = view.turns.values();
  await until(() => turn.runner.display.reply);
  view.tick();
  assert.equal(turn.stateEl.textContent, "");
  assert.ok(!client.calls.some((c) => c[0] === "status"));
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
  view.attach(false);
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
  view.attach(false);
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
    view.attach(false);
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
