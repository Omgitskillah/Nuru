"use strict";

const { FIXTURES } = require("./helpers/setup");
const test = require("node:test");
const assert = require("node:assert/strict");
const { MarkdownRenderer } = require("obsidian");

const { escapeHtml, sanitize } = require("../lib/render");

async function rendered(markdown) {
  const el = createDiv();
  await MarkdownRenderer.render(null, escapeHtml(markdown), el, "", null);
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
  assert.equal(escapeHtml(markdown), "a &lt;b>\n```html\n<div>x</div>\n```\nuse `<br>` here");
});
