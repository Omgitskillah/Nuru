"use strict";
const { until } = require("./helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");
const { Modal } = require("obsidian");
const { HistoryModal, HistoryQuestion, PERMISSION } = require("../lib/history");
const row = (id = "conv00000001") => ({ id, title: "Trip", title_rev: 2, trash_rev: 3 });
const opened = [], ask = HistoryQuestion.ask;
test.afterEach(() => { HistoryQuestion.ask = ask; for (const m of opened.splice(0)) m.close(); });
const button = (m, text) => [...m.contentEl.querySelectorAll("button")].find((b) => b.textContent === text);
async function setup({ writable = true, history, pending = null } = {}) {
  const calls = [];
  const client = {
    async history(options) { calls.push(["history", options]); return history ? history(options) : { conversations: [row()], trash_count: 1 }; },
    async changeHistory(...args) { calls.push(["change", ...args]); },
    async emptyTrash(targets, id) { calls.push(["empty", targets, id]); return { state: "completed", erased: targets.length, skipped: [] }; },
  };
  const options = { writable, getPending: () => pending,
    async savePending(value) { pending = value && JSON.parse(JSON.stringify(value)); },
    async openConversation(id) { calls.push(["open", id]); }, async changed() { calls.push(["changed"]); } };
  const m = new HistoryModal({}, client, options); opened.push(m); m.open(); await until(() => !m.loading);
  return { m, client, calls, options };
}
async function click(m, text) { button(m, text).click(); await until(() => !m.busy && !m.loading); }

test("dialog input, confirm, Cancel and Escape return only the chosen result", async () => {
  let p = HistoryQuestion.ask({}, { title: "Rename", text: "Name", value: "Old", action: "Rename" });
  Modal.shown.input.value = "New"; button(Modal.shown, "Rename").click(); assert.equal(await p, "New");
  p = HistoryQuestion.ask({}, { title: "Erase", text: "Evidence remains", action: "Yes", items: ["<script>unsafe</script>"] }); assert.equal(Modal.shown.contentEl.querySelector("script"), null); assert.match(Modal.shown.contentEl.textContent, /unsafe/); button(Modal.shown, "Yes").click(); assert.equal(await p, true);
  p = HistoryQuestion.ask({}, { title: "Erase", action: "Yes" }); button(Modal.shown, "Cancel").click(); assert.equal(await p, null);
  p = HistoryQuestion.ask({}, { title: "Erase", action: "Yes" }); Modal.shown.close(); assert.equal(await p, null);
});
test("read-only devices retain search and have explained disabled mutations", async () => {
  const { m, calls } = await setup({ writable: false });
  assert.equal(button(m, "Rename").disabled, true); assert.equal(button(m, "Delete").title, PERMISSION);
  await click(m, "Trash (1)"); assert.equal(button(m, "Restore").disabled, true); assert.equal(m.empty.disabled, true);
  await m.beginEmpty(); assert.equal(calls.filter((c) => c[0] === "empty").length, 0);
});
test("stale and closed search replies cannot replace newer results or the search input", async () => {
  const { m, client } = await setup(); const input = m.search; let first, second;
  client.history = () => new Promise((resolve) => { if (!first) first = resolve; else second = resolve; });
  input.value = "old"; const a = m.load(); input.value = "new"; const b = m.load();
  second({ conversations: [{ ...row(), title: "New result" }], trash_count: 2 }); await b;
  first({ conversations: [{ ...row(), title: "Old result" }], trash_count: 1 }); await a;
  assert.equal(m.search, input); assert.equal(input.value, "new"); assert.match(m.rows.textContent, /New result/); assert.doesNotMatch(m.rows.textContent, /Old result/);
  let late; client.history = () => new Promise((resolve) => { late = resolve; }); const c = m.load(); m.close(); late({ conversations: [], trash_count: 0 }); await c;
});
test("pagination, Clear and load errors stay in the history dialog", async () => {
  const { m, calls, client } = await setup({ history: () => ({ conversations: Array.from({ length: 50 }, (_, i) => row(`conv${i}`)), trash_count: 0 }) });
  assert.equal(m.trash.hidden, true); await click(m, "Load more"); assert.equal(calls.at(-1)[1].before, "conv49");
  m.search.value = "query"; await click(m, "Clear"); assert.equal(m.search.value, "");
  client.history = async () => { throw new Error("offline"); }; await m.load(); assert.match(m.note.textContent, /Couldn't load history/);
});
test("rename/delete cancellation has no mutation; accepted actions use revisions and Restore", async () => {
  const { m, calls } = await setup(); HistoryQuestion.ask = async () => null;
  await click(m, "Rename"); await click(m, "Delete"); assert.equal(calls.filter((c) => c[0] === "change").length, 0);
  HistoryQuestion.ask = async () => "Chosen"; await click(m, "Rename");
  assert.deepEqual(calls.find((c) => c[0] === "change"), ["change", row().id, "rename", { title: "Chosen", title_rev: 2 }]);
  HistoryQuestion.ask = async () => true; await click(m, "Delete"); assert.equal(calls.filter((c) => c[0] === "change").at(-1)[2], "hide");
  await click(m, "Trash (1)"); await click(m, "Restore"); assert.equal(calls.filter((c) => c[0] === "change").at(-1)[2], "undo");
});
test("Open is explicit navigation; revocation disables mutations with a useful explanation", async () => {
  const a = await setup(); await click(a.m, "Open"); assert.ok(a.calls.some((c) => c[0] === "open"));
  const { m, client } = await setup(); HistoryQuestion.ask = async () => true;
  client.changeHistory = async () => { throw Object.assign(new Error("forbidden"), { status: 403 }); };
  await click(m, "Delete"); assert.equal(button(m, "Delete").disabled, true); assert.equal(m.note.textContent, PERMISSION);
  await m.run(async () => { throw new Error("conflict"); }); assert.match(m.note.textContent, /conflict/);
});
test("Empty trash confirmation discloses retained content; Cancel never sends", async () => {
  const { m, calls } = await setup(); HistoryQuestion.ask = async (_app, options) => { assert.match(options.text, /write evidence.*logs and OS backups/); return null; };
  await m.beginEmpty(); assert.equal(calls.filter((c) => c[0] === "empty").length, 0);
});
test("multi-batch erasure freezes the confirmed list and never adds new Trash", async () => {
  const all = Array.from({ length: 201 }, (_, i) => row(`conv${i}`));
  const { m, client, calls, options } = await setup();
  client.history = async ({ before }) => { const start = before ? all.findIndex((r) => r.id === before) + 1 : 0; return { conversations: all.slice(start, start + 50), trash_count: all.length }; };
  HistoryQuestion.ask = async () => { all.push(row("new-trash")); return true; };
  await m.beginEmpty(); const sent = calls.filter((c) => c[0] === "empty"); assert.deepEqual(sent.map((c) => c[1].length), [200, 1]);
  assert.ok(sent.every((c) => c[1].every((r) => r.id !== "new-trash"))); assert.equal(options.getPending(), null); assert.match(m.note.textContent, /Erased 201/);
});
test("lost response plus reopen reuses durable targets and the exact request ID", async () => {
  const { m, client, options } = await setup(); HistoryQuestion.ask = async () => true;
  client.emptyTrash = async () => { throw new Error("network"); }; await m.beginEmpty(); const saved = options.getPending(); assert.ok(saved.active.requestId); assert.match(m.note.textContent, /network/);
  const next = new HistoryModal({}, { ...client, emptyTrash: async (targets, id) => { assert.equal(id, saved.active.requestId); assert.deepEqual(targets, saved.active.targets); return { state: "completed", erased: 0, skipped: [{}] }; } }, options);
  opened.push(next); next.open(); await until(() => !next.loading); await click(next, "Check pending erasure"); assert.equal(options.getPending(), null); assert.match(next.note.textContent, /kept 1/);
});
test("server-pending progress remains durable until recovery is observed", async () => {
  const { m, client, options } = await setup(); HistoryQuestion.ask = async () => true; client.emptyTrash = async () => ({ state: "pending", erased: 0, skipped: [] });
  await m.beginEmpty(); assert.ok(options.getPending().active); assert.match(m.note.textContent, /Restart Nuru/); assert.equal(m.retry.hidden, false); await m.beginEmpty();
  client.emptyTrash = async () => ({ state: "completed", erased: 1, skipped: [] }); await click(m, "Check pending erasure"); assert.equal(options.getPending(), null);
});
test("empty lists, inconsistent pagination and failed persistence cannot erase", async () => {
  const { m, client, calls, options } = await setup();
  client.history = async () => ({ conversations: [], trash_count: 0 }); await m.beginEmpty(); assert.match(m.note.textContent, /Trash is empty/);
  client.history = async () => ({ conversations: Array.from({ length: 50 }, () => row()), trash_count: 50 }); await m.beginEmpty(); assert.match(m.note.textContent, /pagination/);
  client.history = async () => ({ conversations: [row()], trash_count: 1 }); HistoryQuestion.ask = async () => true;
  options.savePending = async () => { throw new Error("disk full"); }; await m.beginEmpty(); assert.match(m.note.textContent, /disk full/); assert.equal(calls.filter((c) => c[0] === "empty").length, 0);
});
