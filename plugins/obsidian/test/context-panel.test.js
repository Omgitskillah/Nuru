"use strict";
const { until } = require("./helpers/setup");
require("obsidian");
const test = require("node:test"), assert = require("node:assert/strict");
const { ContextPanel, checked } = require("../lib/context-panel");
const fresh = (extra = {}) => ({ model: "qwen", revision: "r1", window_tokens: 32768, measured: false, can_measure: true, busy: false, ...extra });
const measured = (extra = {}) => fresh({ measured: true, gb: 2, min_gb: 1, max_gb: 4, max_tokens: 65536, bytes_per_token: 65536, ...extra });
const panels = [], roots = [];
test.afterEach(() => { for (const p of panels.splice(0)) p.close(false); for (const r of roots.splice(0)) r.remove(); });
async function setup({ info = fresh(), change, get, ...options } = {}) {
  const root = document.body.createDiv(), anchor = root.createEl("span", { attr: { tabindex: "0" } }); roots.push(root);
  const calls = [], client = { async contextSettings(model) { calls.push(["read", model]); return get ? get() : info; }, async changeContext(...args) { calls.push(["change", ...args]); return change ? change(...args) : measured(); } };
  const panel = new ContextPanel(anchor, { client, model: "qwen", label: "Qwen", ...options }); panels.push(panel); await panel.ready;
  return { panel, anchor, calls };
}
test("opening only reads; measurement is explicit, progress blocks duplicate requests", async () => {
  let finish, reloads = 0;
  const { panel, calls, anchor } = await setup({ change: () => new Promise((r) => { finish = r; }), reloading: () => reloads++ });
  assert.deepEqual(calls, [["read", "qwen"]]); assert.equal(anchor.getAttribute("aria-expanded"), "true");
  assert.ok(panel.minus.disabled && panel.plus.disabled); assert.match(panel.body.textContent, /Only when you press it/);
  panel.action.click(); assert.equal(reloads, 1); assert.equal(panel.action.textContent, "Measuring…"); assert.ok(panel.action.disabled);
  await panel.run("measure"); assert.equal(calls.length, 2); finish(measured()); await until(() => !panel.working);
  assert.equal(panel.action.textContent, "Apply"); assert.ok(panel.action.disabled); assert.match(panel.note.textContent, /measured/);
});
test("steps stage only, bounds hold, Apply sends once and close discards edits", async () => {
  const { panel, calls, anchor } = await setup({ info: measured({ limit_reason: "Measured limit" }), change: () => measured({ gb: 3, revision: "r2", notice: "Applied for now" }) });
  await panel.run("apply"); assert.equal(calls.length, 1);
  panel.plus.click(); assert.equal(panel.pending, 3); assert.equal(calls.length, 1); assert.equal(document.activeElement, panel.plus);
  panel.action.click(); assert.equal(panel.action.textContent, "Reloading the model…"); await until(() => !panel.working);
  assert.deepEqual(calls[1], ["change", "qwen", { operation: "apply", expected_revision: "r1", gb: 3 }]); assert.equal(panel.note.textContent, "Applied for now");
  panel.plus.click(); assert.ok(panel.plus.disabled); panel.plus.click(); assert.equal(panel.pending, 4);
  panel.minus.click(); panel.minus.click(); panel.minus.click(); assert.equal(panel.pending, 1); assert.ok(panel.minus.disabled);
  panel.closeButton.click(); assert.equal(document.activeElement, anchor); assert.equal(calls.length, 2); assert.equal(anchor.getAttribute("aria-expanded"), "false"); panel.close();
});
test("unsupported and busy states never start measurement", async () => {
  for (const options of [{ info: fresh({ can_measure: false, reason: "Claude's window is set by Anthropic" }) }, { info: fresh({ busy: true }) }, { busy: () => true }]) {
    const { panel, calls } = await setup(options); assert.ok(panel.action.disabled); await panel.run("measure"); assert.equal(calls.length, 1);
  }
});
test("lost response blocks retries until reopening to reconcile", async () => {
  const { panel, calls } = await setup({ change: () => { throw new Error("unreachable"); } });
  panel.action.click(); await until(() => !panel.working); assert.match(panel.note.textContent, /Reopen/); assert.ok(panel.action.disabled);
  await panel.run("measure"); assert.equal(calls.length, 2);
});
test("close and navigation ignore late results without repeating or undoing work", async () => {
  for (const close of [true, false]) {
    let finish, current = true;
    const { panel, calls } = await setup({ current: () => current, change: () => new Promise((r) => { finish = r; }) });
    const run = panel.run("measure"); if (close) panel.close(); else current = false;
    finish(measured()); await run; assert.equal(panel.info.measured, false); assert.equal(calls.length, 2); await panel.run("measure");
  }
  const stale = await setup({ current: () => false }); assert.equal(stale.panel.info, undefined);
});
test("Escape and outside close; hostile labels are plain text", async () => {
  const { panel, anchor } = await setup({ label: "<img onerror=bad>" }); assert.equal(panel.el.querySelector("img"), null);
  anchor.dispatchEvent(new Event("pointerdown", { bubbles: true })); assert.equal(panel.closed, false);
  panel.el.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true })); assert.equal(panel.closed, false);
  panel.el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); assert.equal(panel.closed, true);
  const other = await setup(); document.body.dispatchEvent(new Event("pointerdown", { bubbles: true })); assert.equal(other.panel.closed, true);
});
test("malformed, failed and closed settings reads cannot enable controls", async () => {
  for (const get of [() => null, () => { throw new Error("offline"); }]) {
    const { panel } = await setup({ get }); assert.match(panel.note.textContent, /Invalid|offline/); assert.equal(panel.action, undefined);
  }
  let finish; const pending = setup({ get: () => new Promise((r) => { finish = r; }) }); panels.at(-1).close(); finish(fresh());
  const { panel } = await pending; assert.equal(panel.info, undefined);
});
test("malformed server data never drives a memory control", () => {
  assert.equal(checked(measured(), "qwen").gb, 2);
  for (const bad of [null, fresh({ model: "other" }), fresh({ revision: null }), fresh({ window_tokens: 0 }), fresh({ window_tokens: 16777217 }), fresh({ measured: null }), fresh({ can_measure: null }), fresh({ busy: null }), fresh({ reason: {} }), fresh({ notice: {} }), fresh({ limit_reason: {} }), measured({ gb: 0 }), measured({ min_gb: 3 }), measured({ max_gb: 1 }), measured({ max_gb: 4097 }), measured({ max_tokens: 0 }), measured({ bytes_per_token: Infinity }), measured({ bytes_per_token: -1 })]) assert.throws(() => checked(bad, "qwen"));
});
