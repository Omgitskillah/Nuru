// Showing what Nuru says without running or loading any of it. Replies quote notes, and notes can hold
// HTML and images, so:
// 1. before Obsidian renders the Markdown, raw HTML outside code is escaped and shows as text, and
//    images and embeds (![alt](url), ![[note]]) become plain links: the renderer starts loading an
//    image the moment it makes one, before anything after it could stop that, and an embedded note
//    would bring in its own unescaped content;
// 2. after, the rendered tree is cleaned, in case anything got through: no scripts or embedded
//    content, no event attributes, links only to notes in the vault, http(s) or mailto, and any
//    remote image left as a link.
"use strict";

const FENCE = /^(\s*)(`{3,}|~{3,})/;

// `markdown` ready for the renderer: outside code, every "<" written as "&lt;" (raw HTML is text) and
// every "![" as "[" (an image or embed is a link). Fenced blocks and inline code are left alone:
// Markdown shows them literally anyway.
function prepare(markdown) {
  const out = [];
  let fence = null;
  for (const line of markdown.split("\n")) {
    const opening = line.match(FENCE);
    if (fence) {
      if (opening && opening[2][0] === fence[0] && opening[2].length >= fence.length) fence = null;
      out.push(line);
    } else if (opening) {
      fence = opening[2];
      out.push(line);
    } else {
      out.push(line.split(/(`+[^`]*`+)/).map((part, i) => (i % 2 ? part : part.replace(/</g, "&lt;").replace(/!\[/g, "[")))
        .join(""));
    }
  }
  return out.join("\n");
}

const DROP = new Set(["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "FORM", "INPUT", "BUTTON", "TEXTAREA",
  "SELECT", "LINK", "META", "BASE", "SVG", "MATH", "FRAME", "FRAMESET", "AUDIO", "VIDEO", "SOURCE"]);
const SAFE_LINK = /^(https?:|mailto:)/i;

// Clean what Obsidian rendered into `el`, in place.
function sanitize(el) {
  for (const node of [...el.querySelectorAll("*")]) {
    if (DROP.has(node.tagName.toUpperCase())) {
      node.remove();
      continue;
    }
    for (const attribute of [...node.attributes]) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith("on") || name === "style" || name === "srcdoc" || name === "formaction") {
        node.removeAttribute(attribute.name);
      }
    }
    if (node.tagName === "A") {
      const internal = node.classList.contains("internal-link") && node.getAttribute("data-href");
      const href = (node.getAttribute("href") || "").trim();
      if (!internal && !SAFE_LINK.test(href)) node.removeAttribute("href");
      if (!internal) node.removeAttribute("data-href");
    }
    if (node.tagName === "IMG") {
      const src = node.getAttribute("src") || "";
      if (/^(https?:)?\/\//i.test(src) || /^(data|javascript|file):/i.test(src)) {
        const link = node.ownerDocument.createElement("a");
        if (/^https?:/i.test(src)) link.setAttribute("href", src);
        link.textContent = node.getAttribute("alt") || src;
        node.replaceWith(link);
      }
    }
  }
}

module.exports = { prepare, sanitize };
