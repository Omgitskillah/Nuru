"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const { CHAT_MODEL, EMBEDDING_MODEL, check, pull } = require("../lib/ollama");

const ROOT = path.join(__dirname, "..", "..", "..");

// A stand-in Ollama on 127.0.0.1: its version, its models, and /api/pull answering with `pullLines`
// (streamed one per line) or `pullStatus`.
async function ollama(t, { models = [], pullLines = [], pullStatus = 200, stall = false } = {}) {
  const pulls = [];
  const srv = http.createServer((request, response) => {
    if (stall) return undefined;
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (request.url === "/api/version") return response.end(JSON.stringify({ version: "0.40.4" }));
      if (request.url === "/api/tags") return response.end(JSON.stringify({ models: models.map((name) => ({ name })) }));
      pulls.push(JSON.parse(body));
      response.writeHead(pullStatus);
      for (const line of pullLines) response.write(`${JSON.stringify(line)}\n`);
      return response.end(pullStatus === 200 ? "" : '{"error":"something broke"}');
    });
    return undefined;
  });
  await new Promise((resolve) => srv.listen(0, "127.0.0.1", resolve));
  t.after(() => { srv.closeAllConnections(); srv.close(); });
  return { base: `http://127.0.0.1:${srv.address().port}`, pulls };
}

test("check says which needed models Ollama lacks, taking a bare name as :latest", async (t) => {
  const { base } = await ollama(t, { models: ["granite4.2:3b", "llama3:latest"] });
  assert.deepEqual(await check(["granite4.2:3b", "llama3", "nomic-embed-text:v1.5"], { base }),
    { running: true, version: "0.40.4", missing: ["nomic-embed-text:v1.5"] });
});

test("check finds Ollama not running when nothing answers, or it doesn't answer in time", async (t) => {
  assert.deepEqual(await check(["a"], { base: "http://127.0.0.1:1" }), { running: false, version: null, missing: ["a"] });
  const { base } = await ollama(t, { stall: true });
  assert.deepEqual(await check(["a"], { base, timeoutMs: 100 }), { running: false, version: null, missing: ["a"] });
});

test("pull streams Ollama's progress, as a share once it knows the size", async (t) => {
  const { base, pulls } = await ollama(t, { pullLines: [{ status: "pulling manifest" },
    { status: "pulling 1a2b", total: 200, completed: 50 }, { status: "pulling 1a2b", total: 200 }, { status: "success" }] });
  const progress = [];
  await pull("granite4.2:3b", (text) => progress.push(text), { base });
  assert.deepEqual(pulls, [{ model: "granite4.2:3b", stream: true }]);
  assert.deepEqual(progress, ["granite4.2:3b: pulling manifest", "granite4.2:3b: pulling 1a2b 25%",
    "granite4.2:3b: pulling 1a2b 0%", "granite4.2:3b: success"]);
});

test("a pull Ollama reports an error in, or answers with a failure, is refused", async (t) => {
  const failing = await ollama(t, { pullLines: [{ status: "pulling manifest" }, { error: "file does not exist" }] });
  await assert.rejects(pull("nope", undefined, { base: failing.base }), /file does not exist/);
  const broken = await ollama(t, { pullStatus: 500 });
  await assert.rejects(pull("nope", () => {}, { base: broken.base }), /Ollama answered 500: {"error":"something broke"}/);
  await assert.rejects(pull("nope", () => {}, { base: "http://127.0.0.1:1" }), /ECONNREFUSED/);
});

test("a stalled pull or a failing tags list is an error", async (t) => {
  const stalled = await ollama(t, { stall: true });
  await assert.rejects(pull("x", () => {}, { base: stalled.base, timeoutMs: 100 }), /Ollama didn't answer/);
  const srv = http.createServer((request, response) => {
    if (request.url === "/api/version") return response.end('{"version":"1"}');
    response.writeHead(500);
    return response.end();
  });
  await new Promise((resolve) => srv.listen(0, "127.0.0.1", resolve));
  t.after(() => srv.close());
  await assert.rejects(check(["a"], { base: `http://127.0.0.1:${srv.address().port}` }), /^Error: Ollama answered 500$/);
});

test("the plugin's default models are Nuru's own", () => {
  const session = fs.readFileSync(path.join(ROOT, "nuru", "src", "core", "session.py"), "utf8");
  assert.ok(session.includes(`BASE_MODEL = os.environ.get("NURU_BASE_MODEL", "${CHAT_MODEL}")`));
  const semantic = fs.readFileSync(path.join(ROOT, "servers", "obsidian", "src", "obsidian_mcp", "semantic.py"), "utf8");
  assert.ok(semantic.includes(`DEFAULT_MODEL = "${EMBEDDING_MODEL}"`));
});
