"use strict";

require("./helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { NuruClient, NuruError } = require("../lib/client");
const { connect, vaultId, ConnectionProblem } = require("../lib/connection");

const json = (status, value) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

test("a POST whose answer was lost is sent again with the same request ID", async () => {
  const sent = [];
  let calls = 0;
  const client = new NuruClient({ baseUrl: "http://x", token: "t", fetch: async (url, init) => {
    sent.push(JSON.parse(init.body));
    calls += 1;
    if (calls < 3) throw new TypeError("network down");
    return json(202, { turn_id: "turn00000001" });
  } });
  const accepted = await client.submit("conv0000demo", "hi");
  assert.equal(accepted.turn_id, "turn00000001");
  assert.equal(sent.length, 3);
  assert.equal(new Set(sent.map((b) => b.request_id)).size, 1);
});

test("a refusal comes back as its code and message, with no retry", async () => {
  let calls = 0;
  const client = new NuruClient({ baseUrl: "http://x", token: "t", fetch: async () => {
    calls += 1;
    return json(429, { v: 1, error: { code: "queue_full", message: "busy", retry_after_seconds: 5 } });
  } });
  await assert.rejects(client.submit("conv0000demo", "hi"), (error) =>
    error instanceof NuruError && error.code === "queue_full" && error.retry_after_seconds === 5);
  assert.equal(calls, 1);
});

test("every request carries the token", async () => {
  let headers;
  const client = new NuruClient({ baseUrl: "http://x", token: "secret", fetch: async (url, init) => { headers = init.headers; return json(200, {}); } });
  await client.status();
  assert.equal(headers.Authorization, "Bearer secret");
});

function home(token) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-home-"));
  if (token !== undefined) {
    fs.mkdirSync(path.join(dir, ".nuru", "service", "clients"), { recursive: true });
    fs.writeFileSync(path.join(dir, ".nuru", "service", "clients", "obsidian.token"), token);
  }
  return dir;
}

test("connecting says why it can't", async () => {
  const vault = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-vault-"));
  const status = (id) => async () => json(200, { v: 1, service: "nuru", version: "0.1.0", vault: { id, name: "v" }, ready: {} });
  const cases = [
    [{ home: home() }, "no-token"],
    [{ home: home("t"), fetch: async () => { throw new TypeError("refused"); } }, "no-service"],
    [{ home: home("t"), fetch: async () => json(401, { v: 1, error: { code: "unauthorized", message: "no" } }) }, "unauthorized"],
    [{ home: home("t"), fetch: status("0".repeat(32)) }, "wrong-vault"],
  ];
  for (const [options, kind] of cases) {
    await assert.rejects(connect({ vaultPath: vault, ...options }), (e) => e instanceof ConnectionProblem && e.kind === kind, kind);
  }
  const { client } = await connect({ vaultPath: vault, home: home("t\n"), fetch: status(vaultId(vault)) });
  assert.equal(client.token, "t");
});

test("the vault ID follows symlinks to the real folder", () => {
  const vault = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-vault-"));
  const link = `${vault}-link`;
  fs.symlinkSync(vault, link);
  assert.equal(vaultId(link), vaultId(vault));
  assert.match(vaultId(vault), /^[0-9a-f]{32}$/);
});

test("an answer cut off after its status is retried with the same request ID", async () => {
  const sent = [];
  const client = new NuruClient({ baseUrl: "http://x", token: "t", fetch: async (url, init) => {
    sent.push(JSON.parse(init.body).request_id);
    if (sent.length === 1) return { ok: true, status: 202, text: async () => { throw new TypeError("body cut"); } };
    return json(202, { turn_id: "turn00000001", replayed: true });
  } });
  assert.equal((await client.submit("conv0000demo", "hi", [], "req-chosen-by-caller")).turn_id, "turn00000001");
  assert.deepEqual(sent, ["req-chosen-by-caller", "req-chosen-by-caller"]);
});

test("the global fetch is called as a function, as a browser's must be", async () => {
  const real = globalThis.fetch;
  globalThis.fetch = function (url) {
    if (this !== undefined && this !== globalThis) throw new TypeError("Illegal invocation");
    return Promise.resolve(json(200, { v: 1, service: "nuru" }));
  };
  try {
    assert.equal((await new NuruClient({ baseUrl: "http://x", token: "t" }).status()).service, "nuru");
  } finally {
    globalThis.fetch = real;
  }
});

test("when no answer comes, the problem says what failed", async () => {
  const vault = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-vault-"));
  await assert.rejects(connect({ vaultPath: vault, home: home("t"), fetch: async () => { throw new TypeError("Illegal invocation"); } }),
    (e) => e.kind === "no-service" && e.message.includes("TypeError: Illegal invocation"));
});
