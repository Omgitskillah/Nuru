"use strict";

const { FIXTURES, until } = require("./helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");

const { NuruError } = require("../lib/client");
const { TurnRunner } = require("../lib/turns");

// A client whose event streams play `events` from the cursor asked for, breaking after `breakAfter`
// events on the first `drops` calls.
function streaming(events, { drops = 0, breakAfter = 3, error } = {}) {
  const cursors = [];
  return {
    cursors,
    async events(turnId, after, onEvent) {
      cursors.push(after);
      if (error) throw error;
      const rest = events.filter((e) => e.seq > after);
      if (cursors.length <= drops) {
        rest.slice(0, breakAfter).forEach(onEvent);
        throw new NuruError(0, "unreachable", "dropped");
      }
      rest.forEach(onEvent);
    },
  };
}

test("a dropped stream is picked up again from the last event shown", async () => {
  const events = FIXTURES.streams.completed_with_tools;
  const client = streaming(events, { drops: 2 });
  const states = [];
  const runner = new TurnRunner({ client, turnId: "turn00000001", reconnectMs: [1], onChange: (r) => states.push(r.connection) });
  await runner.run();
  assert.deepEqual(client.cursors, [0, 3, 6]);
  assert.equal(runner.display.state, "completed");
  assert.ok(states.includes("reconnecting") && states.at(-1) === "live");
});

test("a turn whose events are gone is lost, not retried", async () => {
  const client = streaming([], { error: new NuruError(410, "cursor_expired", "gone") });
  const runner = new TurnRunner({ client, turnId: "turn00000001", reconnectMs: [1] });
  await runner.run();
  assert.equal(runner.connection, "lost");
  assert.deepEqual(client.cursors, [0]);
});

test("closing the panel stops following, and only that", async () => {
  let cancelled = false;
  const client = {
    events: (turnId, after, onEvent, signal) => new Promise((resolve, reject) =>
      signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })))),
    cancel: async () => { cancelled = true; },
  };
  const runner = new TurnRunner({ client, turnId: "turn00000001" });
  const running = runner.run();
  runner.stop();
  await running;
  assert.equal(cancelled, false, "the turn goes on in the service");
  await runner.cancel();
  assert.equal(cancelled, true);
});

test("a stream ending without a terminal event waits, then resumes from the last displayed cursor", { timeout: 5000 }, async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const cursors = [], states = [];
  const text = { seq: 1, type: "text", data: { text: "hello" } };
  const client = {
    async events(turnId, after, onEvent) {
      cursors.push(after);
      onEvent(text); // the reconnect replays this too: the display must ignore the duplicate
      if (cursors.length === 2) onEvent({ seq: 2, type: "completed", data: { reply: "hello" } });
    },
  };
  const runner = new TurnRunner({ client, turnId: "turn00000001", reconnectMs: [500],
    onChange: (r) => states.push(r.connection) });
  t.after(() => runner.stop());
  const running = runner.run();
  await new Promise(setImmediate);
  assert.deepEqual(cursors, [0]);
  assert.equal(runner.connection, "reconnecting");
  t.mock.timers.tick(499);
  await new Promise(setImmediate);
  assert.deepEqual(cursors, [0], "EOF uses the configured backoff before reconnecting");
  t.mock.timers.tick(1);
  await running;
  assert.deepEqual(cursors, [0, 1]);
  assert.equal(runner.display.reply, "hello");
  assert.equal(runner.display.state, "completed");
  assert.ok(states.includes("reconnecting") && states.at(-1) === "live");
});
