"use strict";
// Server-sent event framing however the bytes arrive (protocol.readEvents).
const test = require("node:test");
const assert = require("node:assert/strict");
const { readEvents } = require("../lib/protocol");

function body(text, size) {
  const bytes = new TextEncoder().encode(text);
  return new ReadableStream({
    start(controller) {
      for (let at = 0; at < bytes.length; at += size) controller.enqueue(bytes.slice(at, at + size));
      controller.close();
    },
  });
}

async function events(text, size) {
  const seen = [];
  await readEvents(body(text, size), (event) => seen.push(event));
  return seen;
}

const FRAMES = [{ seq: 1, type: "reply.delta", text: "Hé" }, { seq: 2, type: "turn.completed" }];
const wire = (newline) => FRAMES.map((event) => `id: ${event.seq}${newline}data: ${JSON.stringify(event)}${newline}${newline}`).join("");

for (const [name, newline] of [["CRLF", "\r\n"], ["LF", "\n"]]) {
  test(`${name} frames give the same events whatever size the reads are, down to one byte`, async () => {
    for (const size of [1, 2, 3, 7, 1024]) {
      assert.deepEqual(await events(wire(newline), size), FRAMES, `${name}, ${size}-byte reads`);
    }
  });
}

test("a CRLF split exactly between the frame's two line ends still ends the frame", async () => {
  const text = wire("\r\n");
  const end = text.indexOf("\r\n\r\n") + 3; // ...\r\n\r | \n...
  const bytes = new TextEncoder().encode(text);
  const split = new TextEncoder().encode(text.slice(0, end)).length;
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(bytes.slice(0, split));
      controller.enqueue(bytes.slice(split));
      controller.close();
    },
  });
  const seen = [];
  await readEvents(stream, (event) => seen.push(event));
  assert.deepEqual(seen, FRAMES);
});
