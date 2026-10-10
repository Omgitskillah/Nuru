// WP01 M1 spike, throwaway. Plain CommonJS, so it needs no build. Checks, in real Obsidian, that a
// plugin can read protocol v1 events from a loopback service as they stream: authenticated fetch, CORS
// from app://obsidian.md, a reader over the body, and resuming from a cursor without showing anything
// twice. Writes what it saw to spike-result.json in the vault, and shows the reply in a sidebar.
const { ItemView, MarkdownRenderer, Notice, Plugin } = require("obsidian");
const fs = require("fs");
const os = require("os");
const path = require("path");

const VIEW = "nuru-spike-view";
const BASE = "http://127.0.0.1:27125";
const TURN = "/v1/turns/turn00000001/events";

class SpikeView extends ItemView {
  getViewType() { return VIEW; }
  getDisplayText() { return "Nuru spike"; }
  getIcon() { return "message-circle"; }
  async onOpen() {
    this.contentEl.empty();
    this.status = this.contentEl.createEl("div", { text: "waiting" });
    this.reply = this.contentEl.createEl("div");
  }
}

// Events from a fetch body, as they arrive. `onEvent` gets each parsed event and the time it came.
async function readEvents(response, onEvent, stopAfter) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let count = 0;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return count;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const data = frame.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
      if (!data) continue; // a keepalive comment
      onEvent(JSON.parse(data), Date.now());
      count += 1;
      if (stopAfter && count >= stopAfter) {
        await reader.cancel(); // what a closed panel or dropped connection does
        return count;
      }
    }
  }
}

module.exports = class NuruSpike extends Plugin {
  async onload() {
    this.registerView(VIEW, (leaf) => new SpikeView(leaf));
    this.addCommand({ id: "run", name: "Stream a fake reply", callback: () => this.run() });
    this.app.workspace.onLayoutReady(() => this.run());
  }

  async onunload() {
    this.app.workspace.detachLeavesOfType(VIEW);
  }

  async view() {
    let leaf = this.app.workspace.getLeavesOfType(VIEW)[0];
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false);
      await leaf.setViewState({ type: VIEW, active: true });
    }
    this.app.workspace.revealLeaf(leaf);
    return leaf.view;
  }

  async run() {
    const result = { obsidian: require("obsidian").apiVersion, origin: window.location.origin, started: new Date().toISOString() };
    const view = await this.view();
    try {
      const token = fs.readFileSync(path.join(os.homedir(), ".nuru", "service", "spike-token"), "utf8").trim();
      const auth = { Authorization: `Bearer ${token}` };

      // A refused request must still be readable, or the plugin can't tell "wrong token" from "no service".
      const refused = await fetch(`${BASE}/v1/status`);
      result.without_token = { status: refused.status, body: await refused.json() };
      const status = await fetch(`${BASE}/v1/status`, { headers: auth });
      result.status = { status: status.status, body: await status.json() };

      // Read four events, drop the connection, resume after the last one applied.
      const seen = [];
      let applied = 0;
      let text = "";
      const show = (event, at) => {
        seen.push({ seq: event.seq, type: event.type, latency_ms: at - Date.parse(event.at), at });
        if (event.seq <= applied) return; // already shown
        applied = event.seq;
        if (event.type === "text") text += event.data.text;
        if (event.type === "completed") text = event.data.reply;
        view.status.setText(`${event.type} (event ${event.seq})`);
        view.reply.setText(text); // as plain text while streaming: no HTML from the stream is run
      };
      const opened = performance.now();
      const first = await fetch(`${BASE}${TURN}?after=0`, { headers: auth });
      result.first_response_ms = Math.round(performance.now() - opened);
      result.first_part = await readEvents(first, show, 4);
      const resumed = await fetch(`${BASE}${TURN}?after=${applied}`, { headers: auth });
      result.second_part = await readEvents(resumed, show);
      const tooFar = await fetch(`${BASE}${TURN}?after=999`, { headers: auth });
      result.past_the_end = { status: tooFar.status, body: await tooFar.json() };

      view.reply.empty();
      await MarkdownRenderer.render(this.app, text, view.reply, "", this);
      result.events = seen;
      result.seqs_in_order = seen.map((e) => e.seq).every((s, i) => s === i + 1);
      result.reply = text;
      result.arrival_gaps_ms = seen.slice(1).map((e, i) => e.at - seen[i].at);
      result.ok = result.without_token.status === 401 && result.status.status === 200 && result.seqs_in_order
        && seen[seen.length - 1].type === "completed" && result.past_the_end.body.error.code === "cursor_expired";
    } catch (error) {
      result.ok = false;
      result.error = String(error && error.stack || error);
      view.status.setText(`failed: ${error}`);
    }
    result.finished = new Date().toISOString();
    await this.app.vault.adapter.write("spike-result.json", JSON.stringify(result, null, 1));
    new Notice(`Nuru spike: ${result.ok ? "passed" : "failed"}`);
  }
};
