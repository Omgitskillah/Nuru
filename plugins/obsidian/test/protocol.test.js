"use strict";

const { FIXTURES } = require("./helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");

const { Display, readEvents, answerText, newRequestId } = require("../lib/protocol");

// A response body delivering `wire`'s UTF-8 bytes `size` at a time: a split can fall inside a character.
function body(wire, size = Infinity) {
  const bytes = new TextEncoder().encode(wire);
  return new ReadableStream({ start(controller) {
    for (let i = 0; i < bytes.length; i += size) controller.enqueue(bytes.slice(i, i + size));
    controller.close();
  } });
}

function shown(events) {
  const display = new Display();
  for (const event of events) display.apply(event);
  return display;
}

test("events are read whole however the bytes are split, keepalives skipped", async () => {
  const events = FIXTURES.streams.completed_with_tools;
  const wire = events.map((e) => `id: ${e.seq}\nevent: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`).join(": keepalive\n\n");
  for (const size of [1, 3, 7, 64, Infinity]) {
    const seen = [];
    await readEvents(body(wire, size), (event) => seen.push(event));
    assert.deepEqual(seen, events, `chunks of ${size}`);
  }
  const crlf = [];
  await readEvents(body(wire.replace(/\n/g, "\r\n"), 5), (event) => crlf.push(event));
  assert.deepEqual(crlf, events);
});

test("a replay from any cursor, or an event applied twice, shows nothing twice", () => {
  for (const [name, events] of Object.entries(FIXTURES.streams)) {
    const whole = shown(events);
    for (let cursor = 0; cursor <= events.length; cursor += 1) {
      const display = shown(events.slice(0, cursor));
      for (const event of [...events.slice(cursor), ...events]) display.apply(event);
      assert.deepEqual(display, whole, `${name} from ${cursor}`);
    }
  }
});

test("a display follows a turn to its end", () => {
  const tools = shown(FIXTURES.streams.completed_with_tools);
  assert.equal(tools.state, "completed");
  assert.ok(tools.tools.every((t) => t.ok !== null));
  assert.equal(shown(FIXTURES.streams.uncertain_write).receipts[0].state, "uncertain");
  const queued = shown(FIXTURES.streams.cancelled_in_queue);
  assert.equal(queued.state, "cancelled");
  const waiting = new Display();
  for (const event of FIXTURES.streams.write_approved.slice(0, 4)) waiting.apply(event);
  assert.equal(waiting.question.question.kind, "yes_no");
  assert.equal(shown(FIXTURES.streams.write_approved).question, null);
});

test("an answer reads as the service words it", () => {
  assert.equal(answerText(["a", "b"]), "a; b");
  assert.equal(answerText([], "mine"), "mine");
  assert.equal(answerText([]), "None of these.");
  assert.match(newRequestId(), /^[A-Za-z0-9_-]{16,128}$/);
});
