// Defence in depth for current events and older saved displays. Sources are admitted by the service,
// never extracted here from a model's reply or arbitrary tool arguments.
"use strict";

const PER_CALL = 32, PER_TURN = 128;
const UNSAFE = new Set('\\:#^[]|%<>"?*');
const CONTROL = /[\u0000-\u001f\u007f\uD800-\uDFFF]/u;
const length = (text) => [...text].length;

function normalize(item) {
  if (!item || typeof item !== "object" || Array.isArray(item)) return null;
  const { path, heading } = item;
  if (typeof path !== "string" || !path || length(path) > 384 || !path.toLowerCase().endsWith(".md")) return null;
  if ([...path].some((c) => UNSAFE.has(c) || CONTROL.test(c))) return null;
  if (path.split("/")[0].toLowerCase() === "trash" || path.split("/").some((part) => !part || part.startsWith(".") || part !== part.trim())) return null;
  if (heading != null && (typeof heading !== "string" || !heading || length(heading) > 160 || CONTROL.test(heading))) return null;
  return { path, ...(heading != null ? { heading } : {}) };
}

function merge(existing, incoming, limit = PER_CALL) {
  const result = [], seen = new Set();
  for (const group of [existing, incoming]) {
    for (const item of (Array.isArray(group) ? group : []).slice(0, limit)) {
      if (result.length >= limit) return result;
      const valid = normalize(item);
      if (valid && !seen.has(valid.path)) { seen.add(valid.path); result.push(valid); }
    }
  }
  return result;
}

module.exports = { normalize, merge, PER_CALL, PER_TURN };
