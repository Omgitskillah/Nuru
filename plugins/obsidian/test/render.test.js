"use strict";

const { FIXTURES } = require("./helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");
const { MarkdownRenderer, window } = require("obsidian");

const { prepare, sanitize } = require("../lib/render");

async function rendered(markdown) {
  const el = createDiv();
  await MarkdownRenderer.render(null, prepare(markdown), el, "", null);
  sanitize(el);
  return el;
}

test("raw HTML in a reply shows as text and nothing in it can run", async () => {
  const reply = FIXTURES.streams.hostile_markdown.at(-1).data.reply;
  const el = await rendered(reply);
  assert.equal(el.querySelector("script, img, iframe, [onerror], [onclick]"), null);
  assert.ok(el.textContent.includes("<script>alert(1)</script>"), "the HTML is shown, as text");
  for (const link of el.querySelectorAll("a")) assert.ok(!link.hasAttribute("href"), link.outerHTML);
});

test("whatever the renderer lets through is cleaned after", () => {
  const el = createDiv();
  el.innerHTML = '<p onclick="x()" style="color:red">t</p><script>x()</script><iframe src="https://e.com"></iframe>' +
    '<a href="javascript:x()">j</a><a href="obsidian://advanced-uri?commandid=x">o</a><a href="https://ok.example">ok</a>' +
    '<a class="internal-link" data-href="nuru/Trip.md" href="nuru/Trip.md">Trip</a>' +
    '<img src="https://tracker.example/p.png" alt="pixel"><img src="app://local/vault/pic.png" alt="local">';
  sanitize(el);
  assert.equal(el.querySelector("script, iframe, [onclick], [style]"), null);
  const links = Object.fromEntries([...el.querySelectorAll("a")].map((a) => [a.textContent, a.getAttribute("href")]));
  assert.deepEqual(links, { j: null, o: null, ok: "https://ok.example", Trip: "nuru/Trip.md",
    pixel: "https://tracker.example/p.png" });
  assert.equal(el.querySelectorAll("img").length, 1, "a vault image stays; a remote one becomes a link");
});

test("code keeps its angle brackets", () => {
  const markdown = "a <b>\n```html\n<div>x</div>\n```\nuse `<br>` here";
  assert.equal(prepare(markdown), "a &lt;b>\n```html\n<div>x</div>\n```\nuse `<br>` here");
});

test("an image or embed in a reply never becomes an element that could load", async () => {
  const el = createDiv();
  const made = [];
  const watcher = new window.MutationObserver((records) => {
    for (const record of records) for (const node of record.addedNodes) {
      if (node.nodeType === 1) made.push(node.tagName, ...[...node.querySelectorAll("*")].map((n) => n.tagName));
    }
  });
  watcher.observe(el, { childList: true, subtree: true });
  const markdown = "Look: ![pixel](https://tracker.example/p.png?from=note) and ![[nuru/Trip.md]] and ![ref][r]\n\n[r]: https://t.example/x.png";
  await MarkdownRenderer.render(null, prepare(markdown), el, "", null);
  await new Promise((resolve) => setTimeout(resolve, 0));
  watcher.disconnect();
  assert.ok(made.length > 0, "the renderer did make elements");
  assert.ok(!made.includes("IMG"), `made ${made}`);
  assert.equal(prepare(markdown).includes("!["), false);
  assert.equal(prepare("`![kept](x)`\n```\n![kept](x)\n```"), "`![kept](x)`\n```\n![kept](x)\n```", "code is left alone");
});
