// Showing what Nuru says without running or loading any of it. Replies quote notes, and notes can hold
// HTML and images, so a reply is rendered by markdown-it, not Obsidian's renderer:
// - raw HTML is off: it shows as text;
// - images are off: ![alt](url) is a "!" and a link, and so is an embed, ![[note]] (an embedded note
//   would bring in its own content). No image element is ever made, so nothing is fetched; a renderer
//   that makes one has already started loading it, before anything after could stop that;
// - [[note]] and [[note|label]] are links to notes in the vault;
// - the HTML is parsed into an inert document (one that loads nothing), cleaned there, and only then
//   copied into the panel: no scripts or embedded content, no event attributes, links only to notes in
//   the vault, http(s) or mailto.
// The parser is the same whatever the text, so unmatched backticks, odd fences or a reply cut off
// mid-stream can't make it treat an image as code or code as anything else.
// What Obsidian's renderer adds (callouts, math, embeds) isn't shown; the reply's text is.
"use strict";

const MarkdownIt = require("markdown-it");

const markdown = new MarkdownIt({ html: false, linkify: true });
markdown.disable("image");

// [[target]] and [[target|label]], as links to notes.
markdown.inline.ruler.before("link", "wikilink", (state, silent) => {
  const start = state.pos;
  if (state.src.charCodeAt(start) !== 0x5b || state.src.charCodeAt(start + 1) !== 0x5b) return false;
  const end = state.src.indexOf("]]", start + 2);
  if (end < 0) return false;
  const inner = state.src.slice(start + 2, end);
  if (!inner.trim() || inner.includes("\n") || inner.includes("[")) return false;
  if (!silent) {
    const [target, label] = inner.split("|", 2);
    const token = state.push("wikilink", "", 0);
    token.meta = { target: target.trim(), label: (label ?? target).trim() };
  }
  state.pos = end + 2;
  return true;
});
markdown.renderer.rules.wikilink = (tokens, index) => {
  const { target, label } = tokens[index].meta;
  const escape = markdown.utils.escapeHtml;
  return `<a class="internal-link" data-href="${escape(target)}" href="${escape(target)}">${escape(label)}</a>`;
};

const DROP = new Set(["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "FORM", "INPUT", "BUTTON", "TEXTAREA",
  "SELECT", "LINK", "META", "BASE", "SVG", "MATH", "FRAME", "FRAMESET", "AUDIO", "VIDEO", "SOURCE", "IMG", "PICTURE"]);
const SAFE_LINK = /^(https?:|mailto:)/i;

// Clean an element tree in place: nothing markdown-it should make anyway, checked once more.
function sanitize(el) {
  for (const node of [...el.querySelectorAll("*")]) {
    if (DROP.has(node.tagName.toUpperCase())) {
      node.remove();
      continue;
    }
    for (const attribute of [...node.attributes]) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith("on") || name === "style" || name === "srcdoc" || name === "formaction" || name === "src") {
        node.removeAttribute(attribute.name);
      }
    }
    if (node.tagName === "A") {
      const internal = node.classList.contains("internal-link") && node.getAttribute("data-href");
      const href = (node.getAttribute("href") || "").trim();
      if (!internal && !SAFE_LINK.test(href)) node.removeAttribute("href");
      if (!internal) node.removeAttribute("data-href");
    }
  }
}

// The reply as HTML: what markdown-it makes of it.
function toHtml(text) {
  return markdown.render(text);
}

// Show `text` (Markdown) in `el`, replacing what was there. Built and cleaned in an inert document first.
function renderReply(text, el) {
  const inert = el.ownerDocument.implementation.createHTMLDocument("");
  const holder = inert.createElement("div");
  holder.innerHTML = toHtml(text);
  sanitize(holder);
  el.replaceChildren(...[...holder.childNodes].map((node) => el.ownerDocument.importNode(node, true)));
}

module.exports = { toHtml, renderReply, sanitize };
