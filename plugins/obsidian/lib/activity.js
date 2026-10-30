// What Nuru did during one turn, in order, from protocol v1 events: its thinking (one block per model
// call), each tool call with its arguments and, when it failed, why, and each retry or steer. Display
// (lib/protocol.js) keeps what the turn shows; this keeps how it got there. Applying an event twice, or
// replaying from an earlier cursor, changes nothing. No Obsidian or DOM here.
"use strict";

const { TERMINAL } = require("./protocol");

// Steps: {kind: "thinking", text, done, at} | {kind: "tool", call_id, name, arguments, ok, summary, at}
//      | {kind: "retry", reason, at}. `at`: when it arrived here, in ms (for the live line's seconds).
class Activity {
  constructor() {
    this.seq = 0;
    this.steps = [];
    this.thinking = null; // the thinking step still streaming, until its call's final reasoning
    this.writingSince = null; // when reply text began after the last step
    this.ended = false;
    this.startedAt = null; // the service's clock, at "started" and at the end: how long the turn took
    this.endedAt = null;
    this.partial = false; // built from a saved Display: tools without arguments, the last call's thinking only
  }

  apply(event) {
    if (event.seq <= this.seq) return false;
    this.seq = event.seq;
    const data = event.data;
    const at = Date.now(); // when the panel learned of it: the service's clock may differ, and a replay is late
    switch (event.type) {
      case "reasoning":
        if (!this.thinking) {
          this.thinking = { kind: "thinking", text: "", done: false, at };
          this.steps.push(this.thinking);
          this.writingSince = null;
        }
        // The final event carries the call's whole reasoning again, replacing its pieces.
        this.thinking.text = data.final ? data.text : this.thinking.text + data.text;
        if (data.final) {
          this.thinking.done = true;
          this.thinking = null;
        }
        break;
      case "tool_start":
        this.steps.push({ kind: "tool", call_id: data.call_id, name: data.name, arguments: data.arguments || {},
          ok: null, summary: null, at });
        this.writingSince = null;
        break;
      case "tool_result":
        for (const step of this.steps) {
          if (step.kind === "tool" && step.call_id === data.call_id) {
            step.ok = data.ok;
            step.summary = data.summary || null;
            step.doneAt = at;
          }
        }
        break;
      case "retry":
        this.closeThinking();
        this.steps.push({ kind: "retry", reason: data.reason, at });
        this.writingSince = null;
        break;
      case "text":
        this.writingSince ??= at;
        break;
      case "started":
        this.startedAt = Date.parse(event.at) || null;
        break;
      default:
        if (TERMINAL.has(event.type)) {
          this.ended = true;
          this.endedAt = Date.parse(event.at) || null;
          this.closeThinking();
        }
    }
    return true;
  }

  // How long the turn ran, in whole seconds, by the service's clock; null if that isn't known.
  seconds() {
    return this.startedAt && this.endedAt ? Math.max(0, Math.round((this.endedAt - this.startedAt) / 1000)) : null;
  }

  closeThinking() {
    if (this.thinking) this.thinking.done = true;
    this.thinking = null;
  }

  // What Nuru is doing now, for the live step line: {what, since} (since: ms), or null once it ended.
  current() {
    if (this.ended) return null;
    // Reply text comes before the call's final reasoning (Ollama sends that at the call's end), so writing
    // wins over a thinking step that is still open.
    if (this.writingSince !== null) return { what: "Writing the reply", since: this.writingSince };
    if (this.thinking) return { what: "Thinking", since: this.thinking.at };
    const running = this.steps.filter((s) => s.kind === "tool" && s.ok === null).at(-1);
    if (running) return { what: `Running ${running.name}`, since: running.at };
    const last = this.steps.at(-1);
    return last ? { what: "Working", since: last.doneAt ?? last.at } : null;
  }

  // The steps a saved Display still knows: its tools (names and outcomes) and the last call's thinking.
  static fromDisplay(display) {
    const activity = new Activity();
    activity.partial = true;
    activity.ended = TERMINAL.has(display.state);
    if (display.reasoning) activity.steps.push({ kind: "thinking", text: display.reasoning, done: true, at: null });
    for (const tool of display.tools || []) {
      activity.steps.push({ kind: "tool", call_id: tool.call_id, name: tool.name, arguments: null, ok: tool.ok,
        summary: null, at: null });
    }
    return activity;
  }
}

// Which argument names a tool call, shown on its line: the first of these it has. Never its content: a
// call without a target would look as if it had one.
const NAMING = ["note", "name", "path", "query", "title", "folder", "tag", "expression"];

function mainArgument(args) {
  if (!args) return "";
  const key = NAMING.find((k) => typeof args[k] === "string" && args[k].trim());
  if (!key) return "";
  const value = args[key].replace(/\s+/g, " ").trim();
  return value.length > 60 ? `${value.slice(0, 59)}…` : value;
}

// Every argument as "name: value" lines (JSON for values that aren't strings), for a call's details.
function argumentLines(args) {
  return Object.entries(args || {}).map(([key, value]) => `${key}: ${typeof value === "string" ? value : JSON.stringify(value)}`);
}

// A retry's reason in words. "silent": the model only reasoned, so it was asked again without thinking.
// "call_format": it wrote a tool call as text; "call_lost": its reply came back empty although it wrote
// something, perhaps a call that couldn't be read. Either way it was asked again for a proper call.
function retryWords(reason) {
  if (reason === "silent") return "Nuru only reasoned and wrote nothing: asked again without thinking";
  if (reason === "call_format") return "Nuru wrote a tool call as text: asked again for a proper call";
  if (reason === "call_lost") return "Nuru's reply came back empty (a tool call may have been lost): asked again for a proper call";
  return `Thinking stopped (${reason}): going on without it`;
}

module.exports = { Activity, mainArgument, argumentLines, retryWords };
