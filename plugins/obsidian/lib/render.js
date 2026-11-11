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
const sourceTargets = require("./sources");

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
function renderReply(text, el, sources = []) {
  const inert = el.ownerDocument.implementation.createHTMLDocument("");
  const holder = inert.createElement("div");
  holder.innerHTML = toHtml(text);
  sanitize(holder);
  linkSources(holder, sources);
  el.replaceChildren(...[...holder.childNodes].map((node) => el.ownerDocument.importNode(node, true)));
}


// A relative internal link made only from admitted source metadata. Labels never become HTML.
function sourceLink(parent, item, label) {
  const source = sourceTargets.normalize(item);
  if (!source) return null;
  const target = source.path + (source.heading ? `#${source.heading}` : "");
  const link = parent.ownerDocument.createElement("a");
  link.className = "internal-link nuru-source";
  link.textContent = label ?? target;
  link.setAttribute("href", target);
  link.setAttribute("data-href", target);
  parent.appendChild(link);
  return link;
}

// Only complete literal paths in the admitted set, with or without .md. Existing Markdown links
// keep their meaning. A prefix of another filename or a URL is never promoted to an internal link.
function linkSources(el, sources) {
  const admitted = sourceTargets.merge([], sources, sourceTargets.PER_TURN);
  if (!admitted.length) return;
  const variants = new Map(admitted.map((item) => [item.path, item]));
  for (const item of admitted) {
    const short = item.path.slice(0, -3);
    if (admitted.some((other) => other.path === short)) continue; // exact paths take precedence
    if (variants.has(short) && variants.get(short)?.path !== item.path) variants.set(short, null);
    else variants.set(short, item);
  }
  const names = [...variants].filter(([, item]) => item).map(([name]) => name).sort((a, b) => b.length - a.length);
  const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(names.map(escape).join("|"), "gu");
  const word = (char) => char && /[\p{L}\p{N}_\/.%\\:#?~-]/u.test(char);
  const doc = el.ownerDocument, walker = doc.createTreeWalker(el, 4); // SHOW_TEXT
  const nodes = [];
  while (walker.nextNode()) if (!walker.currentNode.parentElement?.closest("a")) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const text = node.textContent, fragment = doc.createDocumentFragment();
    let end = 0, changed = false;
    for (const match of text.matchAll(pattern)) {
      const at = match.index, after = at + match[0].length;
      let beforeChar = text.charAt(at - 1);
      if (beforeChar >= "\uDC00" && beforeChar <= "\uDFFF") beforeChar = text.slice(at - 2, at);
      const charAt = (index) => index < text.length ? String.fromCodePoint(text.codePointAt(index)) : "";
      const nextChar = charAt(after);
      const sentenceEnd = nextChar === "." && !word(charAt(after + 1));
      if (word(beforeChar) || (word(nextChar) && !sentenceEnd) || !variants.get(match[0])) continue;
      fragment.appendChild(doc.createTextNode(text.slice(end, at)));
      sourceLink(fragment, variants.get(match[0]), match[0]);
      end = after;
      changed = true;
    }
    if (changed) {
      fragment.appendChild(doc.createTextNode(text.slice(end)));
      node.replaceWith(fragment);
    }
  }
}

module.exports = { toHtml, renderReply, sanitize, sourceLink, linkSources };
