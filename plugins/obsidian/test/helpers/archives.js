// Writes small .zip and .tar.gz files for the setup and unpack tests, with any names, modes and flags,
// including the unsafe ones the reader must refuse.
"use strict";

const zlib = require("node:zlib");

// entries: {name, data = "", mode = 0o100644, method = 8, flags = 0, size}; size overrides the
// recorded unpacked size.
function zip(entries) {
  const locals = [];
  const central = [];
  let offset = 0;
  for (const { name, data = "", mode = name.endsWith("/") ? 0o040755 : 0o100644, method = 8, flags = 0, size } of entries) {
    const raw = Buffer.from(data);
    const packed = method === 8 ? zlib.deflateRawSync(raw) : raw;
    const fileName = Buffer.from(name);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(flags, 6);
    local.writeUInt16LE(method, 8);
    local.writeUInt32LE(packed.length, 18);
    local.writeUInt32LE(size ?? raw.length, 22);
    local.writeUInt16LE(fileName.length, 26);
    const header = Buffer.alloc(46);
    header.writeUInt32LE(0x02014b50, 0);
    header.writeUInt16LE(0x0314, 4); // made on Unix, so the mode is in the upper half of the attributes
    header.writeUInt16LE(flags, 8);
    header.writeUInt16LE(method, 10);
    header.writeUInt32LE(packed.length, 20);
    header.writeUInt32LE(size ?? raw.length, 24);
    header.writeUInt16LE(fileName.length, 28);
    header.writeUInt32LE((mode << 16) >>> 0, 38);
    header.writeUInt32LE(offset, 42);
    locals.push(local, fileName, packed);
    central.push(header, fileName);
    offset += local.length + fileName.length + packed.length;
  }
  const directory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, directory, end]);
}

// entries: {name, data = "", mode = 0o644, type = "0", prefix = "", ustar = true, sizeField}; ustar false
// writes an old (v7) header, and sizeField replaces the size as written.
function tarGz(entries) {
  const blocks = [];
  for (const { name, data = "", mode = 0o644, type = "0", prefix = "", ustar = true, sizeField } of entries) {
    const body = Buffer.from(data);
    const header = Buffer.alloc(512);
    header.write(name, 0, 100);
    header.write(`${mode.toString(8).padStart(7, "0")}\0`, 100);
    header.write(sizeField ?? `${body.length.toString(8).padStart(11, "0")}\0`, 124);
    header.write(type, 156);
    if (ustar) {
      header.write("ustar\0", 257, "latin1");
      header.write(prefix, 345, 155);
    }
    blocks.push(header, body, Buffer.alloc((512 - (body.length % 512)) % 512));
  }
  return zlib.gzipSync(Buffer.concat([...blocks, Buffer.alloc(1024)]));
}

module.exports = { tarGz, zip };
