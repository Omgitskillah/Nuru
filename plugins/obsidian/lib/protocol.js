// nuru-serve protocol v1, as a client sees it (nuru/docs/obsidian-remote/protocol-v1.md): reading a
// turn's server-sent events, and Display, the client's rule for showing a turn. Display mirrors
// core.service.protocol.Display in Python; test/protocol.test.js checks both agree on the frozen
// fixture streams. No Obsidian or DOM here.
"use strict";

const crypto = require("node:crypto");

const VERSION = 1;
const TERMINAL = new Set(["completed", "failed", "cancelled"]);

// A request ID: client-chosen, unique, and reused only to retry the same request.
function newRequestId() {
  return `req-${crypto.randomUUID()}`;
}

// Calls onEvent(event) for each event in an SSE body (a fetch Response.body), in order, as frames
// complete. Keepalive comments are skipped. Resolves when the body ends; rejects if it breaks.
async function readEvents(body, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      // Normalized across the whole buffer: a CRLF split between two reads still joins up.
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
      let end;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const data = frame.split("\n").filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(line.startsWith("data: ") ? 6 : 5));
        if (data.length) onEvent(JSON.parse(data.join("\n")));
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// What a client shows for one turn, built by applying its events. Applying an event twice, or
// replaying from any earlier cursor, changes nothing: events at or below `seq` are dropped.
class Display {
  constructor() {
    this.seq = 0;
    this.reply = "";
    this.reasoning = "";
    this.tools = []; // {call_id, name, ok: null | true | false}
    this.receipts = [];
    this.question = null; // the open one, waiting for an answer
    this.decisions = [];
    this.state = "accepted"; // accepted, queued, running, or the terminal type
    this.ask = null;
    this.truncated = false;
    this.message = "";
    this.position = null; // place in the queue while queued
  }

  apply(event) {
    if (event.seq <= this.seq) return false;
    this.seq = event.seq;
    const data = event.data;
    switch (event.type) {
      case "queued": this.state = "queued"; this.position = data.position; break;
      case "started": this.state = "running"; this.position = null; break;
      case "text": this.reply += data.text; break;
      case "reasoning": this.reasoning = data.final ? data.text : this.reasoning + data.text; break;
      case "tool_start": this.tools.push({ call_id: data.call_id, name: data.name, ok: null }); break;
      case "tool_result":
        for (const tool of this.tools) if (tool.call_id === data.call_id) tool.ok = data.ok;
        break;
      case "write_receipt": this.receipts.push(data); break;
      case "question": this.question = data; break;
      case "question_closed": this.question = null; this.decisions.push(data); break;
      default:
        if (TERMINAL.has(event.type)) {
          this.state = event.type;
          this.truncated = Boolean(data.truncated);
          if (event.type === "completed") {
            this.reply = data.reply;
            this.ask = data.ask || null;
          } else if (event.type === "failed") {
            this.message = data.message;
          } else if ("partial_reply" in data) {
            this.reply = data.partial_reply;
          }
        }
    }
    return true;
  }

  get ended() {
    return TERMINAL.has(this.state);
  }

  // A Display from a history entry's `display` (the service's Display as JSON), to go on from.
  static from(saved) {
    const display = new Display();
    Object.assign(display, saved);
    return display;
  }
}

// What an answer to `chosen` (and `other`, the user's own words) reads as, as questions.answer_text:
// the labels and then `other`, joined with "; ", or "None of these." for an empty answer.
function answerText(chosen, other) {
  const parts = [...chosen, ...(other ? [other] : [])];
  return parts.length ? parts.join("; ") : "None of these.";
}

module.exports = { VERSION, TERMINAL, newRequestId, readEvents, Display, answerText };
