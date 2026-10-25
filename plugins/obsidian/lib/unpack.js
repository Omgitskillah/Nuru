// Reads the archives "Set up Nuru" downloads (Nuru's .zip, uv's .zip or .tar.gz) and writes their files.
// Every entry is checked before anything is written: only plain files and folders under the one expected
// top folder, with names that are safe on Windows and macOS, within count and size limits. Links,
// devices, absolute names and ".." are refused. The archives' bytes are hash-checked before they get
// here; this is the second line. No system tar and no zip library: the same code runs everywhere.
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const zlib = require("node:zlib");

const LIMITS = { entries: 20000, bytes: 512 * 1024 * 1024 };
const RESERVED = /^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$/i; // Windows device names, in any folder

class ArchiveProblem extends Error {}

// The path below `root` ("nuru-0.1.0/"), split into parts; null for the root folder itself.
function relative(name, root) {
  if (!name.startsWith(root)) throw new ArchiveProblem(`${JSON.stringify(name)} isn't under ${root}`);
  const rest = name.slice(root.length).replace(/\/$/, "");
  if (!rest) return null;
  const parts = rest.split("/");
  for (const part of parts) {
    if (!part || part === "." || part === ".." || /[\\:*?"<>|\0-\x1f]/.test(part) || /[. ]$/.test(part) || RESERVED.test(part)) {
      throw new ArchiveProblem(`unsafe name ${JSON.stringify(name)}`);
    }
  }
  return parts;
}

function readZip(buffer, root, limits = LIMITS) {
  const end = buffer.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]));
  if (end < 0 || end + 22 > buffer.length) throw new ArchiveProblem("not a zip file");
  const count = buffer.readUInt16LE(end + 10);
  if (count > limits.entries) throw new ArchiveProblem(`more than ${limits.entries} entries`);
  let at = buffer.readUInt32LE(end + 16);
  const entries = [];
  let total = 0;
  for (let i = 0; i < count; i++) {
    if (at + 46 > buffer.length || buffer.readUInt32LE(at) !== 0x02014b50) throw new ArchiveProblem("a damaged zip directory");
    const [flags, method] = [buffer.readUInt16LE(at + 8), buffer.readUInt16LE(at + 10)];
    const [size, unpacked] = [buffer.readUInt32LE(at + 20), buffer.readUInt32LE(at + 24)];
    const [nameLength, extraLength, commentLength] = [28, 30, 32].map((o) => buffer.readUInt16LE(at + o));
    const mode = buffer.readUInt32LE(at + 38) >>> 16;
    const local = buffer.readUInt32LE(at + 42);
    const name = buffer.toString("utf8", at + 46, at + 46 + nameLength);
    at += 46 + nameLength + extraLength + commentLength;
    const parts = relative(name, root);
    const type = mode & 0o170000;
    if (name.endsWith("/")) {
      if (type && type !== 0o040000) throw new ArchiveProblem(`${name} isn't a plain folder`);
      continue;
    }
    if ((type && type !== 0o100000) || !parts) throw new ArchiveProblem(`${name} isn't a plain file`);
    if (flags & 1 || (method !== 0 && method !== 8)) throw new ArchiveProblem(`${name} is encrypted or compressed in an unknown way`);
    if ((total += unpacked) > limits.bytes) throw new ArchiveProblem(`more than ${limits.bytes} bytes`);
    if (buffer.readUInt32LE(local) !== 0x04034b50) throw new ArchiveProblem(`a damaged zip entry, ${name}`);
    const start = local + 30 + buffer.readUInt16LE(local + 26) + buffer.readUInt16LE(local + 28);
    const packed = buffer.subarray(start, start + size);
    const data = method === 8 ? zlib.inflateRawSync(packed, { maxOutputLength: unpacked + 1 }) : packed;
    if (data.length !== unpacked) throw new ArchiveProblem(`${name} isn't the size the zip says`);
    entries.push({ parts, executable: Boolean(mode & 0o100), data });
  }
  return entries;
}

function readTarGz(buffer, root, limits = LIMITS) {
  let tar;
  try {
    tar = zlib.gunzipSync(buffer, { maxOutputLength: limits.bytes + (limits.entries + 2) * 1024 });
  } catch (error) {
    throw new ArchiveProblem(error.code === "ERR_BUFFER_TOO_LARGE" ? `more than ${limits.bytes} bytes` : "not a .tar.gz file");
  }
  const entries = [];
  for (let at = 0; at + 512 <= tar.length;) {
    const header = tar.subarray(at, at + 512);
    if (header.every((byte) => byte === 0)) break;
    const text = (from, length) => header.toString("utf8", from, from + length).replace(/\0.*$/s, "");
    const prefix = header.toString("latin1", 257, 263) === "ustar\0" ? text(345, 155) : "";
    const name = (prefix ? `${prefix}/` : "") + text(0, 100);
    const size = /^[0-7]+$/.test(text(124, 12).trim()) ? parseInt(text(124, 12).trim(), 8) : NaN;
    if (Number.isNaN(size)) throw new ArchiveProblem("a damaged tar header");
    const type = String.fromCharCode(header[156] || 0x30);
    if (entries.length >= limits.entries) throw new ArchiveProblem(`more than ${limits.entries} entries`);
    const parts = relative(name, root);
    if (type === "0" && parts) {
      const data = tar.subarray(at + 512, at + 512 + size);
      if (data.length !== size) throw new ArchiveProblem(`${name} is cut short`);
      entries.push({ parts, executable: Boolean(parseInt(text(100, 8).trim(), 8) & 0o100), data });
    } else if (type !== "5") {
      throw new ArchiveProblem(`${name} isn't a plain file or folder`);
    }
    at += 512 + Math.ceil(size / 512) * 512;
  }
  return entries;
}

// Writes the entries into `dir`, a new empty folder: never over a file, so a name that appears twice
// (or twice in different case, on Windows and macOS) is refused rather than overwritten.
function writeEntries(entries, dir) {
  for (const { parts, executable, data } of entries) {
    const file = path.join(dir, ...parts);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, data, { flag: "wx", mode: executable ? 0o755 : 0o644 });
  }
}

module.exports = { ArchiveProblem, LIMITS, readZip, readTarGz, writeEntries };
