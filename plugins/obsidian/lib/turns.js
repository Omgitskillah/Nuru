// Following one turn: read its events into a Display until it ends, reconnecting from the last event
// applied when the connection drops. No DOM here; the view listens through onChange.
"use strict";

const { Display } = require("./protocol");
const { NuruError } = require("./client");

const RECONNECT_MS = [500, 1000, 2000, 5000]; // then every 5 s

function sleep(ms, signal) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => { clearTimeout(timer); resolve(); }, { once: true });
  });
}

class TurnRunner {
  // onChange(runner) after every change to `display` or `connection`.
  constructor({ client, turnId, display = new Display(), onChange = () => {}, reconnectMs = RECONNECT_MS }) {
    this.client = client;
    this.turnId = turnId;
    this.display = display;
    this.onChange = onChange;
    this.reconnectMs = reconnectMs;
    this.connection = "live"; // live, reconnecting, or lost (the turn can't be followed; resync from history)
    this.problem = null; // the NuruError that ended following, when `connection` is lost
    this.controller = new AbortController();
  }

  // Follow the turn until it ends, the turn is lost, or stop(). Resolves then.
  async run() {
    const signal = this.controller.signal;
    for (let failures = 0; !this.display.ended && !signal.aborted;) {
      try {
        await this.client.events(this.turnId, this.display.seq, (event) => {
          if (this.display.apply(event)) {
            failures = 0;
            this.set("live");
          }
        }, signal);
        if (this.display.ended || signal.aborted) break;
        failures += 1; // the service closed the stream without ending the turn (it is shutting down, say)
      } catch (error) {
        if (signal.aborted || error.name === "AbortError") break;
        if (error instanceof NuruError && [404, 410].includes(error.status)) {
          this.problem = error; // the turn is gone, or its events are: only its history can show it now
          this.set("lost");
          return;
        }
        failures += 1;
      }
      this.set("reconnecting");
      await sleep(this.reconnectMs[Math.min(failures, this.reconnectMs.length) - 1] ?? 0, signal);
    }
  }

  set(connection) {
    this.connection = connection;
    this.onChange(this);
  }

  // Stop following (the panel closed). The turn itself goes on in the service.
  stop() {
    this.controller.abort();
  }

  // Ask the service to stop the turn: Stop. Its `cancelled` event arrives on the stream.
  cancel() {
    return this.client.cancel(this.turnId);
  }
}

module.exports = { TurnRunner, RECONNECT_MS };
