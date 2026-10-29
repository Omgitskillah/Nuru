"use strict";

require("./helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { NuruClient, NuruError } = require("../lib/client");
const { connect, vaultId, vaultUuid, ConnectionProblem } = require("../lib/connection");

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

const OWN = "5f0c7f4e-8a43-4f53-9a51-2a1b2c3d4e5f";

function idVault(id = OWN) {
  const vault = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-vault-"));
  if (id) {
    fs.mkdirSync(path.join(vault, ".nuru"));
    fs.writeFileSync(path.join(vault, ".nuru", "vault-id"), `${id}\n`);
  }
  return vault;
}

test("connecting says why it can't", async () => {
  const vault = idVault();
  const urls = [];
  const status = (uuid) => async (url) => {
    urls.push(url);
    return json(200, { v: 2, service: "nuru", version: "0.1.0", vault: { uuid, name: "v" }, ready: {} });
  };
  const cases = [
    [{ home: home() }, "no-token"],
    [{ home: home("t"), fetch: async () => { throw new TypeError("refused"); } }, "no-service"],
    [{ home: home("t"), fetch: async () => json(401, { v: 1, error: { code: "unauthorized", message: "no" } }) }, "unauthorized"],
    [{ home: home("t"), fetch: status("00000000-0000-4000-8000-000000000000") }, "wrong-vault"],
  ];
  for (const [options, kind] of cases) {
    await assert.rejects(connect({ vaultPath: vault, ...options }), (e) => e instanceof ConnectionProblem && e.kind === kind, kind);
  }
  const { client } = await connect({ vaultPath: vault, home: home("t\n"), fetch: status(OWN) });
  assert.equal(client.token, "t");
  assert.ok(urls.every((url) => url.endsWith("/v2/status")));
});

test("a Nuru that speaks only protocol v1 asks to be updated, and the path hash is never compared", async () => {
  const vault = idVault();
  const old = async () => json(400, { v: 1, error: { code: "unsupported_version", message: "this service speaks protocol version 1" } });
  await assert.rejects(connect({ vaultPath: vault, home: home("t"), fetch: old }),
    (e) => e instanceof ConnectionProblem && e.kind === "old-service" && /git pull/.test(e.message));
  const hashOnly = async () => json(200, { v: 2, service: "nuru", version: "0", vault: { uuid: undefined, id: vaultId(vault), name: "v" }, ready: {} });
  await assert.rejects(connect({ vaultPath: vault, home: home("t"), fetch: hashOnly }), (e) => e.kind === "wrong-vault");
});

test("a vault Nuru has never served has no ID, so it never matches", async () => {
  const vault = idVault(null);
  assert.equal(vaultUuid(vault), null);
  const someone = async () => json(200, { v: 2, service: "nuru", version: "0", vault: { uuid: OWN, name: "Other" }, ready: {} });
  await assert.rejects(connect({ vaultPath: vault, home: home("t"), fetch: someone }), (e) => e.kind === "wrong-vault");
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

// TODO F22: the model settings routes (model-settings-v1.schema.json), each with its method, path and body,
// and a refusal (a stale save's 409) as its code.
test("model settings are read, saved, reset and added on their own routes, names and revisions escaped", async () => {
  const sent = [];
  const client = new NuruClient({ baseUrl: "http://x", token: "t", fetch: async (url, init) => {
    sent.push([init.method, url, init.body ? JSON.parse(init.body) : undefined]);
    if (init.method === "PUT") return json(409, { v: 1, error: { code: "conflict", message: "reload them" } });
    return json(200, { family: "llama3.1" });
  } });
  assert.deepEqual(await client.modelSettings("ns/llama3.1:8b"), { family: "llama3.1" });
  await assert.rejects(client.saveModelSettings({ model: "llama3.1:8b", expected_revision: "initial", header: "h", profile: "p" }),
    (error) => error instanceof NuruError && error.code === "conflict" && error.status === 409);
  await client.resetModelSettings("llama3.1:8b", "a&b");
  await client.addModel("llama3.1:8b");
  assert.deepEqual(sent, [
    ["GET", "http://x/v1/models/settings?model=ns%2Fllama3.1%3A8b", undefined],
    ["PUT", "http://x/v1/models/settings", { model: "llama3.1:8b", expected_revision: "initial", header: "h", profile: "p" }],
    ["DELETE", "http://x/v1/models/settings?model=llama3.1%3A8b&expected_revision=a%26b", undefined],
    ["POST", "http://x/v1/models", { model: "llama3.1:8b" }],
  ]);
});

test("request lookup and question answers keep their route and payload contract", async () => {
  const sent = [];
  const client = new NuruClient({ baseUrl: "http://local", token: "t", fetch: async (url, init) => {
    sent.push({ url, method: init.method, body: init.body && JSON.parse(init.body) });
    return json(200, { ok: true });
  } });
  assert.deepEqual(await client.requestState("req/a?b", "submit_turn"), { ok: true });
  await client.answer("question/a?b", "choose", ["one"], "my answer");
  await client.answer("question/a?b", "choose", [], "");
  assert.deepEqual(sent[0], { url: "http://local/v1/requests/req%2Fa%3Fb?operation=submit_turn", method: "GET", body: undefined });
  assert.equal(sent[1].url, "http://local/v1/questions/question%2Fa%3Fb/answers");
  assert.equal(sent[1].method, "POST");
  assert.deepEqual({ ...sent[1].body, request_id: "chosen" },
    { request_id: "chosen", operation: "choose", chosen: ["one"], other: "my answer" });
  assert.ok(!Object.hasOwn(sent[2].body, "other"), "an empty free-text answer is omitted");
  assert.notEqual(sent[1].body.request_id, sent[2].body.request_id);
});

test("event streaming passes the cursor and abort signal and delivers complete events", async () => {
  const controller = new AbortController();
  const event = { seq: 8, type: "text", data: { text: "hello" } };
  const seen = [];
  const client = new NuruClient({ baseUrl: "http://local", token: "t", fetch: async (url, init) => {
    assert.equal(url, "http://local/v1/turns/turn%2Fa%3Fb/events?after=7");
    assert.equal(init.signal, controller.signal);
    return new Response(`data: ${JSON.stringify(event)}\n\n`, { status: 200 });
  } });
  await client.events("turn/a?b", 7, (received) => seen.push(received), controller.signal);
  assert.deepEqual(seen, [event]);
});

for (const phase of ["request", "body"]) {
  test(`an aborted ${phase} is propagated without retrying the POST`, async () => {
    const aborted = Object.assign(new Error("cancelled"), { name: "AbortError" });
    let attempts = 0;
    const client = new NuruClient({ baseUrl: "http://local", token: "t", fetch: async () => {
      attempts += 1;
      if (phase === "request") throw aborted;
      return { ok: true, status: 202, text: async () => { throw aborted; } };
    } });
    await assert.rejects(client.submit("conversation", "hello"), (error) => error === aborted);
    assert.equal(attempts, 1);
  });
}

test("a repeatedly lost POST stops after three attempts and keeps its caller's request ID", async () => {
  const sent = [];
  const client = new NuruClient({ baseUrl: "http://local", token: "t", fetch: async (url, init) => {
    sent.push(JSON.parse(init.body));
    throw new TypeError("connection closed");
  } });
  await assert.rejects(client.submit("conversation", "hello", [], "req-owned-by-caller"),
    (error) => error instanceof NuruError && error.status === 0);
  assert.equal(sent.length, 3);
  assert.ok(sent.every((body) => body.request_id === "req-owned-by-caller" && body.text === "hello"));
});

test("conversation list, detail, creation and cancellation use the public request contract", async () => {
  const sent = [];
  const client = new NuruClient({ baseUrl: "http://local", token: "t", fetch: async (url, init) => {
    sent.push({ url, method: init.method, body: init.body && JSON.parse(init.body) });
    return json(200, { ok: true });
  } });
  await client.conversations(7);
  await client.conversation("conv/a?b", 12);
  await client.createConversation("A title", "req-create-with-title");
  await client.createConversation("", "req-create-untitled");
  await client.cancel("turn/a?b");
  assert.deepEqual(sent.slice(0, 2), [
    { url: "http://local/v1/conversations?limit=7", method: "GET", body: undefined },
    { url: "http://local/v1/conversations/conv%2Fa%3Fb?limit=12", method: "GET", body: undefined },
  ]);
  assert.deepEqual(sent[2], { url: "http://local/v1/conversations", method: "POST",
    body: { request_id: "req-create-with-title", title: "A title" } });
  assert.deepEqual(sent[3].body, { request_id: "req-create-untitled" });
  assert.equal(sent[4].url, "http://local/v1/turns/turn%2Fa%3Fb/cancel");
  assert.equal(sent[4].method, "POST");
  assert.deepEqual(Object.keys(sent[4].body), ["request_id"]);
});
