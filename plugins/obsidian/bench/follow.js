// The plugin's side of the WP01 benchmark (nuru/evals/obsidian_remote/bench_wp01.py runs it). It takes
// one JSON command per stdin line and answers each with one JSON line:
//   {"op": "turn", "text": …}   send it from the real ChatView's composer; time each chunk to when it
//                               is in the panel's DOM (after the view's own render scheduling)
//   {"op": "stop"}              submit scenario:slow, Stop at its first text, time the acknowledgement
//   {"op": "cycles", "n": 20}   open the real ChatView, send a turn, wait for it to end and render, close
//                               it, n times; count what's left
//   {"op": "live", "turns": […], "answer": "Yes"|"No"|null}   a live-model case (live_wp01.py): the turns in
//                               one new conversation, each question answered so (null: left to expire)
// Times are wall-clock ms (performance.timeOrigin + now), comparable with Python's time.time() * 1000.
// Uses the jsdom stand-in for Obsidian (test/helpers): this times the plugin's code, not Obsidian's.
"use strict";

require("../test/helpers/setup");
const readline = require("node:readline");
const { Component, window } = require("obsidian");
const { connect } = require("../lib/connection");
const { TurnRunner } = require("../lib/turns");
const { ChatView } = require("../lib/view");

const now = () => performance.timeOrigin + performance.now();
const STAMP = /⟦t=([\d.]+)⟧/g;
const [port, vaultPath, home] = process.argv.slice(2);

const app = { workspace: { getActiveFile: () => null, getActiveViewOfType: () => null,
  openLinkText: () => {}, getLeavesOfType: () => [] } };
const plugin = {
  settings: { port: Number(port), conversationId: null },
  vaultPath: () => vaultPath,
  remember: async (changes) => Object.assign(plugin.settings, changes),
  connect: () => connect({ port: Number(port), vaultPath, home }),
};

async function openView() {
  const view = new ChatView({ app }, plugin);
  await view.open();
  await until(() => view.client);
  return view;
}

// Send `text` as a person would (composer, Send) and wait for the new turn to end and its reply to be on
// screen. onRendered(turn) runs on every DOM change in the panel once the turn exists.
async function sendAndShow(view, text, onRendered = () => {}) {
  const before = new Set(view.turns.keys());
  let turn = null;
  const observer = new window.MutationObserver(() => { if (turn) onRendered(turn); });
  observer.observe(view.messagesEl, { childList: true, subtree: true, characterData: true });
  view.input.value = text;
  const sending = view.send();
  await until(() => (turn = [...view.turns].find(([id]) => !before.has(id))?.[1] ?? null));
  await sending;
  await until(() => turn.runner.display.ended && turn.renderedReply === turn.runner.display.reply);
  observer.disconnect();
  return turn;
}

let benchView = null;

async function turn(text) {
  benchView ??= await openView();
  const client = benchView.client;
  const submit = client.submit;
  let accepted = null;
  client.submit = async (...args) => { const answer = await submit.apply(client, args); accepted = now(); return answer; };
  const seen = new Set();
  const overheads = [];
  let firstRender = null;
  const begun = now();
  const shown = await sendAndShow(benchView, text, (t) => {
    const at = now();
    for (const [, stamp] of t.replyEl.textContent.matchAll(STAMP)) {
      if (seen.has(stamp)) continue;
      seen.add(stamp);
      firstRender ??= at;
      overheads.push(at - Number(stamp));
    }
  });
  client.submit = submit;
  return { accepted_ms: accepted - begun, first_render_ms: firstRender - begun, completed_ms: now() - begun,
    state: shown.runner.display.state, overheads_ms: overheads };
}

async function stop(client, conversation) {
  const { turn_id: turnId } = await client.submit(conversation, "scenario:slow");
  let asked = null;
  let ended = null;
  let ack = null;
  const runner = new TurnRunner({ client, turnId, onChange: (r) => {
    if (r.display.ended && ended === null) ended = now();
    if (asked === null && r.display.reply) {
      asked = now();
      r.cancel().then(() => { ack = now(); });
    }
  } });
  await runner.run();
  while (ack === null) await new Promise((resolve) => setTimeout(resolve, 1));
  return { stop_ack_ms: ack - asked, stop_cancelled_ms: ended - asked, state: runner.display.state };
}

async function live(client, turns, answer) {
  const { conversation_id: conversation } = await client.createConversation("live");
  const results = [];
  for (const text of turns) {
    const begun = now();
    const { turn_id: turnId } = await client.submit(conversation, text);
    const answered = new Set();
    const runner = new TurnRunner({ client, turnId, onChange: (r) => {
      const asked = r.display.question;
      if (asked && answer && !answered.has(asked.question_id)) {
        answered.add(asked.question_id);
        client.answer(asked.question_id, asked.operation, [answer]).catch(() => {});
      }
    } });
    await runner.run();
    const { display, activity } = runner;
    results.push({ text, reply: display.reply, state: display.state, message: display.message,
      seconds: (now() - begun) / 1000, questions: answered.size, decisions: display.decisions,
      tools: activity.steps.filter((s) => s.kind === "tool").map((s) => ({ name: s.name, arguments: s.arguments, ok: s.ok })),
      receipts: display.receipts.map((r) => ({ state: r.state, path: r.path ?? null })) });
  }
  return { turns: results };
}

function counts() {
  global.gc();
  return { listeners: Component.listeners, intervals: Component.intervals,
    resources: process.getActiveResourcesInfo().filter((r) => r !== "TTYWrap").length,
    heap_used: process.memoryUsage().heapUsed };
}

async function cycles(n) {
  const once = async () => {
    const view = await openView();
    await sendAndShow(view, "scenario:completed");
    await view.close();
  };
  if (benchView) await benchView.close();
  benchView = null;
  await once(); // warm
  const warm = counts();
  for (let i = 0; i < n; i++) await once();
  await new Promise((resolve) => setTimeout(resolve, 50));
  return { warm, after: counts() };
}

async function until(condition, ms = 10000) {
  const deadline = Date.now() + ms;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error("gave up waiting");
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}

(async () => {
  const { client } = await connect({ port: Number(port), vaultPath, home });
  let conversation = null;
  const lines = readline.createInterface({ input: process.stdin });
  for await (const line of lines) {
    const command = JSON.parse(line);
    if (command.op !== "live" && conversation === null) {
      ({ conversation_id: conversation } = await client.createConversation("bench"));
    }
    const result = command.op === "turn" ? await turn(command.text)
      : command.op === "stop" ? await stop(client, conversation)
        : command.op === "live" ? await live(client, command.turns, command.answer) : await cycles(command.n);
    process.stdout.write(JSON.stringify(result) + "\n");
  }
})().catch((error) => { console.error(error); process.exit(1); });
