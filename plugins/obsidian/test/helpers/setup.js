// Makes require("obsidian") load the stand-in (helpers/obsidian.js), and gives tests a few shared
// helpers. Require it first in every test file that loads plugin code.
"use strict";

const Module = require("node:module");
const path = require("node:path");

const fake = path.join(__dirname, "obsidian.js");
const resolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  return request === "obsidian" ? fake : resolve.call(this, request, ...rest);
};

const ROOT = path.resolve(__dirname, "..", "..", "..", "..");
const FIXTURES = require(path.join(ROOT, "nuru", "tests", "fixtures", "protocol_v1.json"));

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Waits for `condition()` to hold, checking every few milliseconds, for at most `ms`.
async function until(condition, ms = 5000) {
  const deadline = Date.now() + ms;
  while (!(await condition())) {
    if (Date.now() > deadline) throw new Error("gave up waiting");
    await sleep(10);
  }
}

module.exports = { ROOT, FIXTURES, sleep, until };
