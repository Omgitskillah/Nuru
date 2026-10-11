"use strict";

const { FIXTURES } = require("./helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");

const { Activity, mainArgument, argumentLines, retryWords } = require("../lib/activity");
const { Display } = require("../lib/protocol");

function stepsOf(events) {
  const activity = new Activity();
  for (const event of events) activity.apply(event);
  return activity;
}

// Events numbered from 1, with the given types and data.
function events(...pairs) {
  return pairs.map(([type, data], i) => ({ v: 1, conversation_id: "conv0000demo", turn_id: "turn00000001", seq: i + 1,
    at: "2026-10-11T12:00:00.000Z", type, data }));
}

const bare = (activity) => activity.steps.map(({ at, doneAt, ...step }) => step);

test("a turn's steps come in order: its thinking, each tool call with its arguments and result", () => {
  assert.deepEqual(bare(stepsOf(FIXTURES.streams.completed_with_tools)), [
    { kind: "thinking", text: "The user wants the meeting notes.", done: true },
    { kind: "tool", call_id: "call00search", name: "search", arguments: { query: "meeting notes Grüße" }, ok: true,
      summary: "3 notes" },
  ]);
  const refused = stepsOf(FIXTURES.streams.write_refused).steps[0];
  assert.equal(refused.ok, false);
  assert.equal(refused.summary, "refused");
  assert.equal(refused.arguments.note, "shared/Team plan.md");
});

test("each model call's thinking is its own block, and the final reasoning replaces its pieces", () => {
  const activity = stepsOf(events(
    ["started", {}],
    ["reasoning", { text: "Read the ", final: false }], ["reasoning", { text: "note.", final: false }],
    ["tool_start", { call_id: "call00000001", name: "read_note", arguments: { name: "nuru/daily.md" } }],
    ["reasoning", { text: "Read the note first.", final: true }],
    ["tool_result", { call_id: "call00000001", ok: true }],
    ["reasoning", { text: "It already has them.", final: false }],
    ["text", { text: "Done." }],
  ));
  assert.deepEqual(bare(activity).map((s) => [s.kind, s.text ?? s.name, s.done]), [
    ["thinking", "Read the note first.", true],
    ["tool", "read_note", undefined],
    ["thinking", "It already has them.", false],
  ]);
});

test("a retry is a step, and closes the thinking before it", () => {
  const activity = stepsOf(FIXTURES.streams.retry);
  assert.deepEqual(bare(activity), [
    { kind: "thinking", text: "The answer is 42.", done: true },
    { kind: "retry", reason: "silent" },
  ]);
  assert.match(retryWords("silent"), /only reasoned.*again without thinking/);
  assert.equal(retryWords("over budget"), "Thinking stopped (over budget): going on without it");
});

test("a replay from any cursor, or an event applied twice, adds no step twice", () => {
  for (const [name, stream] of Object.entries(FIXTURES.streams)) {
    const whole = bare(stepsOf(stream));
    for (let cursor = 0; cursor <= stream.length; cursor += 1) {
      const activity = stepsOf(stream.slice(0, cursor));
      for (const event of [...stream.slice(cursor), ...stream]) activity.apply(event);
      assert.deepEqual(bare(activity), whole, `${name} from ${cursor}`);
    }
  }
});

test("what Nuru is doing now: thinking, running a tool, writing the reply, nothing once it ended", () => {
  const stream = events(
    ["started", {}],
    ["reasoning", { text: "Search.", final: false }],
    ["tool_start", { call_id: "call00000001", name: "search", arguments: { query: "x" } }],
    ["reasoning", { text: "Search.", final: true }],
    ["tool_result", { call_id: "call00000001", ok: true }],
    ["text", { text: "Found" }],
    ["completed", { reply: "Found" }],
  );
  const activity = new Activity();
  const seen = stream.map((event) => { activity.apply(event); return activity.current()?.what ?? null; });
  assert.deepEqual(seen, [null, "Thinking", "Thinking", "Running search", "Working", "Writing the reply", null]);
});

test("a saved display gives what it kept: the tools without arguments and the last thinking", () => {
  const display = new Display();
  for (const event of FIXTURES.streams.completed_with_tools) display.apply(event);
  const activity = Activity.fromDisplay(JSON.parse(JSON.stringify(display)));
  assert.ok(activity.partial && activity.ended);
  assert.deepEqual(activity.steps.map((s) => [s.kind, s.name ?? s.text, s.arguments ?? null, s.ok ?? null]), [
    ["thinking", "The user wants the meeting notes.", null, null],
    ["tool", "search", null, true],
  ]);
});

test("a call's line names it by its note, path or query; its details list every argument", () => {
  assert.equal(mainArgument({ content: "- [ ] a", note: "nuru/daily-2026-10-11.md", mode: "append" }), "nuru/daily-2026-10-11.md");
  assert.equal(mainArgument({ expression: "2 + 2" }), "2 + 2");
  assert.equal(mainArgument({ limit: 3 }), "");
  assert.equal(mainArgument({ content: "Giulia Bona\n- Birthday", type: "person" }), "", "never named by its content");
  assert.equal(mainArgument({ query: "a\n".repeat(50) }).length, 60);
  assert.deepEqual(argumentLines({ note: "x.md", limit: 3, tags: ["a"] }), ["note: x.md", "limit: 3", 'tags: ["a"]']);
});
