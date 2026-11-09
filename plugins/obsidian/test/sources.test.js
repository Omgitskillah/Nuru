"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { normalize, merge, PER_CALL, PER_TURN } = require("../lib/sources");

test("only bounded portable relative Markdown sources are admitted", () => {
  for (const item of [null, 1, [], {}, { path: 2 }, { path: "" }, ...[
    "/a.md", "../a.md", "a/../b.md", "a//b.md", "a/./b.md", "a/.hidden.md", "trash/a.md", "TRASH/a.md",
    "a\\b.md", "a.md#heading", "a[1].md", "a|b.md", "a%20b.md", "C:/a.md", "a /b.md", "a/ b.md",
    "a\x00.md", "a\x7f.md", "a\ud800.md", "a.txt", "x".repeat(385) + ".md",
  ].map((path) => ({ path })), ...["", 2, "x".repeat(161), "bad\nheading", "bad\ud800"].map((heading) => ({ path: "a.md", heading }))]) {
    assert.equal(normalize(item), null, JSON.stringify(item));
  }
  assert.deepEqual(normalize({ path: "nuru/🌍 Grüße.MD", heading: "My   heading" }), { path: "nuru/🌍 Grüße.MD", heading: "My   heading" });
  assert.deepEqual(normalize({ path: "Projects/Trash/Ideas.md" }), { path: "Projects/Trash/Ideas.md" });
  assert.deepEqual(normalize({ path: "Projects/trash/Ideas.md" }), { path: "Projects/trash/Ideas.md" });
  assert.deepEqual(normalize({ path: "a.md" }), { path: "a.md" });
  assert.deepEqual(normalize({ path: "a.md", heading: null }), { path: "a.md" });
});

test("merge preserves the first read and heading and caps both inputs", () => {
  assert.deepEqual(merge([{ path: "a.md", heading: "First" }], [{ path: "a.md", heading: "Later" }, null, { path: "../bad.md" }, { path: "b.md" }]),
    [{ path: "a.md", heading: "First" }, { path: "b.md" }]);
  assert.deepEqual(merge(null, {}), []);
  assert.equal(merge([], Array.from({ length: 50 }, (_, i) => ({ path: `${i}.md` }))).length, PER_CALL);
  assert.deepEqual(merge([{ path: "a.md" }], [{ path: "b.md" }], 1), [{ path: "a.md" }]);
  assert.equal(PER_TURN, 128);
});
