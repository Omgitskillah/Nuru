// Nuru's settings as the settings tab shows them: read from and written to the env files in the Nuru
// folder (.env.nuru, .env.deep-think), the same files Nuru reads, so editing either the tab or a file
// works. Nothing goes into plugin data, which lives in the vault and syncs: the Claude key would travel
// with it. A write changes one line, the active one Nuru reads (the last `KEY=`), or adds it at the end;
// comments, commented-out alternatives and every other line stay as they are.
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

// What the tab can change, and the file each lives in.
const SETTINGS = {
  NURU_BASE_MODEL: ".env.nuru",
  NURU_MAIN_MODEL: ".env.nuru",
  NURU_KEEP_ALIVE: ".env.nuru",
  CLAUDE_API_KEY: ".env.deep-think",
};

const LINE = /^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/;

// A value as python-dotenv reads it: quoted up to its closing quote, else up to a ` #` comment.
function parseValue(raw) {
  const quoted = /^"((?:[^"\\]|\\.)*)"/.exec(raw) || /^'([^']*)'/.exec(raw);
  if (quoted) {
    const value = raw[0] === '"' ? quoted[1].replace(/\\(["\\])/g, "$1") : quoted[1];
    return { value, rest: raw.slice(quoted[0].length) };
  }
  const comment = raw.search(/\s+#/);
  return comment < 0 ? { value: raw.trim(), rest: "" } : { value: raw.slice(0, comment).trim(), rest: raw.slice(comment) };
}

function formatValue(value) {
  return /^[A-Za-z0-9_.:/@+,=-]*$/.test(value) ? value : `"${value.replace(/(["\\])/g, "\\$1")}"`;
}

function lines(file) {
  try {
    return fs.readFileSync(file, "utf8").split("\n");
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
}

// {KEY: value} for the file's active lines; a key set twice is the later line, as Nuru reads it.
function readEnv(file) {
  const values = {};
  for (const line of lines(file)) {
    const match = LINE.exec(line);
    if (match) values[match[2]] = parseValue(match[3]).value;
  }
  return values;
}

// Set KEY in `file`: its last active line is rewritten (keeping an `export` and a trailing comment),
// or the line is added. The file is replaced in one step and keeps its permissions (a new one is 0600);
// a `secret` leaves it readable by its owner only, whatever it was.
function setEnv(file, key, value, { secret = false } = {}) {
  if (/[\r\n]/.test(value)) throw new Error(`${key} can't hold a line break`);
  const all = lines(file);
  const at = all.findLastIndex((line) => LINE.exec(line)?.[2] === key);
  if (at >= 0) {
    const [, prefix, , raw] = LINE.exec(all[at]);
    all[at] = `${prefix}${key}=${formatValue(value)}${parseValue(raw).rest}`;
  } else {
    if (all.length && all.at(-1) === "") all.pop();
    all.push(`${key}=${formatValue(value)}`, "");
  }
  const mode = secret ? 0o600 : fs.statSync(file, { throwIfNoEntry: false })?.mode & 0o777 || 0o600;
  const temporary = `${file}.${process.pid}.tmp`;
  try {
    fs.writeFileSync(temporary, all.join("\n"), { mode });
    fs.chmodSync(temporary, mode);
    fs.renameSync(temporary, file);
  } catch (error) {
    fs.rmSync(temporary, { force: true });
    throw error;
  }
}

class NuruSettings {
  constructor(nuru, environment = process.env) {
    this.nuru = nuru;
    this.environment = environment;
  }

  file(key) {
    return path.join(this.nuru, SETTINGS[key]);
  }

  get(key) {
    return readEnv(this.file(key))[key];
  }

  set(key, value) {
    setEnv(this.file(key), key, value, { secret: key === "CLAUDE_API_KEY" });
  }

  // Set in Obsidian's own environment, which Nuru started from here inherits and prefers over the file.
  overridden(key) {
    const names = key === "CLAUDE_API_KEY" ? ["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"] : [key];
    return names.find((name) => this.environment[name]) || null;
  }

  // The model new conversations start on: Claude when NURU_MAIN_MODEL names it, else NURU_BASE_MODEL.
  defaultModel() {
    const main = this.get("NURU_MAIN_MODEL") || "";
    return main.startsWith("anthropic:") ? main : this.get("NURU_BASE_MODEL") || null;
  }

  // An ID from the service's model list: an Ollama tag, or anthropic:<model>.
  setDefaultModel(id) {
    if (id.startsWith("anthropic:")) {
      this.set("NURU_MAIN_MODEL", id);
      return;
    }
    this.set("NURU_BASE_MODEL", id);
    if (this.get("NURU_MAIN_MODEL") && this.get("NURU_MAIN_MODEL") !== "ollama") this.set("NURU_MAIN_MODEL", "ollama");
  }
}

// Enough to tell two keys apart, never enough to use one.
const fingerprint = (key) => crypto.createHash("sha256").update(key).digest("hex").slice(0, 8);

module.exports = { NuruSettings, SETTINGS, fingerprint, readEnv, setEnv };
