// Connecting the plugin to the local nuru-serve: its token, read from a file outside the vault (never
// from plugin data, which syncs), and the check that the service serves this very vault.
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { NuruClient, NuruError } = require("./client");

const DEFAULT_PORT = 27125;

// Where `nuru-serve token add obsidian` writes the plugin's token.
function tokenPath(home = os.homedir()) {
  return path.join(home, ".nuru", "service", "clients", "obsidian.token");
}

// The vault's identity as nuru-serve reports it (core.service.protocol.vault_id): the first 32 hex
// digits of the SHA-256 of its absolute path with symlinks resolved, as UTF-8.
function vaultId(vaultPath) {
  return crypto.createHash("sha256").update(fs.realpathSync(vaultPath), "utf8").digest("hex").slice(0, 32);
}

// Why the plugin can't chat, in words for the settings and the sidebar.
const PROBLEMS = {
  "no-token": "No token for this plugin. Run `uv run nuru-serve token add obsidian` in the Nuru folder.",
  "no-service": "Nuru isn't running. Start it with `uv run nuru-serve` in the Nuru folder.",
  unauthorized: "Nuru refused this plugin's token. Add a new one with `uv run nuru-serve token add obsidian`.",
  "wrong-vault": "Nuru is serving a different vault from this one, so the plugin won't chat here.",
  "other": "Nuru answered with an error.",
};

class ConnectionProblem extends Error {
  constructor(kind, detail = "") {
    super(PROBLEMS[kind] + (detail ? ` (${detail})` : ""));
    this.kind = kind;
  }
}

// A client for the service on `port`, once its status shows it serves `vaultPath`; a
// ConnectionProblem saying why not otherwise.
async function connect({ port = DEFAULT_PORT, vaultPath, home, fetch } = {}) {
  let token;
  try {
    token = fs.readFileSync(tokenPath(home), "utf8").trim();
  } catch {
    throw new ConnectionProblem("no-token");
  }
  if (!token) throw new ConnectionProblem("no-token");
  const client = new NuruClient({ baseUrl: `http://127.0.0.1:${port}`, token, fetch });
  let status;
  try {
    status = await client.status();
  } catch (error) {
    if (!(error instanceof NuruError)) throw error;
    if (error.status === 0) throw new ConnectionProblem("no-service", error.detail); // what failed, for when it isn't that
    if (error.status === 401) throw new ConnectionProblem("unauthorized");
    throw new ConnectionProblem("other", error.message);
  }
  if (status.vault.id !== vaultId(vaultPath)) {
    throw new ConnectionProblem("wrong-vault", `it serves "${status.vault.name}"`);
  }
  return { client, status };
}

module.exports = { DEFAULT_PORT, tokenPath, vaultId, connect, ConnectionProblem };
