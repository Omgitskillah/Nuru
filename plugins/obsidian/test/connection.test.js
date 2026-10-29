"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { NuruClient } = require("../lib/client");
const { connect, tokenPath, vaultUuid, ConnectionProblem } = require("../lib/connection");

function scratch(t, token = "local-token") {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "nuru-connection-"));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  fs.mkdirSync(path.dirname(tokenPath(home)), { recursive: true });
  fs.writeFileSync(tokenPath(home), token);
  return home;
}

test("an empty token is refused before contacting any service", async (t) => {
  const home = scratch(t, " \r\n\t");
  let sent = 0;
  await assert.rejects(connect({ home, vaultPath: home, fetch: async () => { sent += 1; } }),
    (error) => error instanceof ConnectionProblem && error.kind === "no-token");
  assert.equal(sent, 0);
});

test("a service refusal keeps its explanation without exposing the token", async (t) => {
  const home = scratch(t, "secret-not-for-error-messages");
  let sent = 0;
  await assert.rejects(connect({ home, vaultPath: home, fetch: async () => {
    sent += 1;
    return new Response(JSON.stringify({ v: 1, error: { code: "forbidden", message: "unexpected Host" } }),
      { status: 403, headers: { "Content-Type": "application/json" } });
  } }), (error) => {
    assert.ok(error instanceof ConnectionProblem);
    assert.equal(error.kind, "other");
    assert.match(error.message, /unexpected Host/);
    assert.ok(!error.message.includes("secret-not-for-error-messages"));
    return true;
  });
  assert.equal(sent, 1, "a refused status check is not retried");
});

test("a programming failure in status propagates unchanged", async (t) => {
  const home = scratch(t);
  const failure = new ReferenceError("status decoder defect");
  t.mock.method(NuruClient.prototype, "status", async () => { throw failure; });
  await assert.rejects(connect({ home, vaultPath: home }), (error) => error === failure);
});

test("an invalid vault identity never authenticates a matching service identity", async (t) => {
  const home = scratch(t);
  fs.writeFileSync(path.join(home, ".nuru", "vault-id"), "not-a-uuid\n");
  assert.equal(vaultUuid(home), null);
  await assert.rejects(connect({ home, vaultPath: home, fetch: async () => new Response(JSON.stringify({
    v: 2, service: "nuru", version: "1.0.4", vault: { uuid: "not-a-uuid", name: "Other vault" }, ready: {},
  }), { status: 200 }) }), (error) => error.kind === "wrong-vault" && error.vaultName === "Other vault");
});
