"use strict";

const { FIXTURES } = require("./helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");
const { window } = require("obsidian");

const { toHtml, renderReply, sanitize } = require("../lib/render");

// Every element made while rendering `text` into a panel element: an image would load the moment it
// was made, so none may ever be.
function rendered(text) {
  const el = createDiv();
  document.body.appendChild(el);
  const made = [];
  const watcher = new window.MutationObserver((records) => {
    for (const record of records) for (const node of record.addedNodes) {
      if (node.nodeType === 1) made.push(node.tagName, ...[...node.querySelectorAll("*")].map((n) => n.tagName));
    }
  });
  watcher.observe(el, { childList: true, subtree: true });
  renderReply(text, el);
  made.push(...watcher.takeRecords().flatMap((r) => [...r.addedNodes].filter((n) => n.nodeType === 1)
    .flatMap((n) => [n.tagName, ...[...n.querySelectorAll("*")].map((c) => c.tagName)])));
  watcher.disconnect();
  el.remove();
  return { el, made };
}

// Markdown that a pattern for "code" gets wrong, and replies cut off mid-stream.
const BYPASSES = [
  "`` ![pixel](http://127.0.0.1:9/pixel?from=unmatched-code) `",
  "    ```\n![pixel](http://127.0.0.1:9/pixel?from=indented-fence)",
  "```\n![pixel](http://127.0.0.1:9/pixel?from=unclosed-fence)",
  "~~~~\n```\n~~~\n![pixel](http://127.0.0.1:9/pixel?from=mixed-fences)",
  "![pixel](http://127.0.0.1:9/pixel?from=cut",
  "![ref][r]\n\n[r]: http://127.0.0.1:9/pixel?from=reference",
  "![[nuru/Trip.md]] and ![[photo.png]]",
  "`` <img src=http://127.0.0.1:9/pixel?from=html-in-unmatched-code> `",
  "<img src=http://127.0.0.1:9/pixel?from=html onerror=alert(1)><picture><source srcset=http://127.0.0.1:9/p></picture>",
];

test("no reply, however its Markdown is formed, makes an image or anything that loads", () => {
  for (const text of [...BYPASSES, FIXTURES.streams.hostile_markdown.at(-1).data.reply]) {
    assert.doesNotMatch(toHtml(text), /<(img|picture|source|iframe|script)\b/i, text);  // as text, "&lt;img" is fine
    const { el, made } = rendered(text);
    assert.ok(made.length > 0, "something was shown");
    assert.ok(!made.some((tag) => ["IMG", "PICTURE", "SOURCE", "IFRAME", "SCRIPT"].includes(tag)), `${text} made ${made}`);
    assert.equal(el.querySelector("[onerror], [onclick], [style], [src]"), null, text);
  }
});

test("raw HTML shows as text; only notes, web and mail links stay links", () => {
  const { el } = rendered(FIXTURES.streams.hostile_markdown.at(-1).data.reply);
  assert.ok(el.textContent.includes("<script>alert(1)</script>"));
  for (const link of el.querySelectorAll("a")) assert.ok(!link.hasAttribute("href"), link.outerHTML);
  const links = rendered("[web](https://ok.example) [mail](mailto:a@b.c) [uri](obsidian://advanced-uri?commandid=x) " +
    "[js](javascript:alert(1)) [[nuru/Trip.md|Trip]] and https://bare.example").el;
  assert.deepEqual(Object.fromEntries([...links.querySelectorAll("a")].map((a) => [a.textContent, a.getAttribute("href")])),
    { web: "https://ok.example", mail: "mailto:a@b.c", uri: null, Trip: "nuru/Trip.md", "https://bare.example": "https://bare.example" });
  assert.ok(links.textContent.includes("[js](javascript:alert(1))"), "a javascript: link isn't made a link at all");
});

test("code stays code, and Markdown still reads as Markdown", () => {
  const { el } = rendered("Found **3** notes: [[Grüße]] and [[🌍 Trip]].\n\n```py\nif a < b: print(\"<div>\")\n```\n\nuse `<br>` here");
  assert.equal(el.querySelector("strong").textContent, "3");
  assert.deepEqual([...el.querySelectorAll("a.internal-link")].map((a) => a.getAttribute("data-href")), ["Grüße", "🌍 Trip"]);
  assert.equal(el.querySelector("pre code").textContent, "if a < b: print(\"<div>\")\n");
  assert.equal(el.querySelectorAll("code")[1].textContent, "<br>");
});

test("whatever gets through is cleaned", () => {
  const el = createDiv();
  el.innerHTML = '<p onclick="x()" style="color:red">t</p><script>x()</script><img src="https://e.com/p.png">' +
    '<a href="javascript:x()">j</a><a class="internal-link" data-href="nuru/Trip.md" href="nuru/Trip.md">Trip</a>';
  sanitize(el);
  assert.equal(el.querySelector("script, img, [onclick], [style]"), null);
  assert.deepEqual([...el.querySelectorAll("a")].map((a) => a.getAttribute("href")), [null, "nuru/Trip.md"]);
});
