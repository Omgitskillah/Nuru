// Ollama, which runs Nuru's local models: whether it answers on this computer, which of the models Nuru
// needs it has, and downloading one it lacks through its own API. Installing Ollama itself is left to
// its installer: the settings tab says where to get it.
"use strict";

const http = require("node:http");

const OLLAMA = "http://127.0.0.1:11434";
const DOWNLOAD = "https://ollama.com/download";
// Nuru's own defaults, for an install with no .env.nuru: core/session.py's BASE_MODEL, and the notes
// server's embedding model (servers/obsidian semantic.py's DEFAULT_MODEL), which every install needs.
const CHAT_MODEL = "qwen3.5:4b";
const EMBEDDING_MODEL = "nomic-embed-text:v1.5";

// "name" and "name:latest" are one model to Ollama.
const tagged = (name) => (name.includes(":") ? name : `${name}:latest`);

function request(method, path, { body, base = OLLAMA, timeoutMs = 5000, onLine } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(new URL(path, base), { method, timeout: timeoutMs, headers: body ? { "content-type": "application/json" } : {} }, (response) => {
      let text = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        text += chunk;
        if (!onLine) return;
        const lines = text.split("\n");
        text = lines.pop();
        try {
          for (const line of lines) if (line.trim()) onLine(JSON.parse(line));
        } catch (error) {
          req.destroy();
          reject(error);
        }
      });
      response.on("end", () => {
        if (response.statusCode !== 200) return reject(new Error(`Ollama answered ${response.statusCode}${text ? `: ${text.trim()}` : ""}`));
        resolve(onLine ? null : JSON.parse(text));
      });
    });
    req.on("timeout", () => req.destroy(new Error("Ollama didn't answer")));
    req.on("error", reject);
    req.end(body ? JSON.stringify(body) : undefined);
  });
}

// {running, version, missing}: the models in `needed` Ollama doesn't have; running false when it
// doesn't answer (not installed, or not started).
async function check(needed, options = {}) {
  let version;
  try {
    ({ version } = await request("GET", "/api/version", options));
  } catch {
    return { running: false, version: null, missing: [...needed] };
  }
  const { models } = await request("GET", "/api/tags", options);
  const have = new Set(models.map((m) => tagged(m.name)));
  return { running: true, version, missing: needed.filter((name) => !have.has(tagged(name))) };
}

// Downloads `name` into Ollama, calling onProgress(text) as it goes. Models are gigabytes, so there is
// no time limit beyond Ollama's own between its progress lines.
async function pull(name, onProgress = () => {}, options = {}) {
  await request("POST", "/api/pull", { ...options, timeoutMs: options.timeoutMs ?? 120 * 1000, body: { model: name, stream: true },
    onLine: (line) => {
      if (line.error) throw new Error(line.error);
      const share = line.total ? ` ${Math.floor((100 * (line.completed || 0)) / line.total)}%` : "";
      onProgress(`${name}: ${line.status}${share}`);
    } });
}

module.exports = { CHAT_MODEL, DOWNLOAD, EMBEDDING_MODEL, OLLAMA, check, pull };
