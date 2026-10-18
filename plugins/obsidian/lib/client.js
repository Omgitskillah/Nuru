// A client of nuru-serve's protocol v1 over fetch. Every POST carries a request ID; when its response
// is lost (the connection drops, before or after the status line), it is sent again with the same ID,
// which the service answers with the result it already gave instead of acting twice. A caller that
// must not lose track of a request (a message to Nuru) chooses the ID itself and keeps it until it
// knows what became of the request.
"use strict";

const { newRequestId, readEvents } = require("./protocol");

class NuruError extends Error {
  // `status` 0 means no answer at all: the service isn't running, or the connection broke.
  constructor(status, code, message, extra = {}) {
    super(message);
    this.status = status;
    this.code = code;
    Object.assign(this, extra); // retry_after_seconds, resync
  }
}

const RETRIES = 2; // a POST whose response was lost is sent this many more times

class NuruClient {
  // `fetch` defaults to the global one, always called as a plain function: a browser's (Obsidian's) fetch
  // throws "Illegal invocation" when called as a method of another object.
  constructor({ baseUrl, token, fetch = (...args) => globalThis.fetch(...args) }) {
    this.baseUrl = baseUrl;
    this.token = token;
    this.fetch = fetch;
  }

  async request(method, path, body, { signal } = {}) {
    let response;
    try {
      response = await this.fetch(this.baseUrl + path, {
        method,
        headers: { Authorization: `Bearer ${this.token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
        body: body ? JSON.stringify(body) : undefined,
        signal,
      });
    } catch (error) {
      if (error.name === "AbortError") throw error;
      throw new NuruError(0, "unreachable", "Nuru isn't answering. Is nuru-serve running?",
        { detail: `${error.name}: ${error.message}` });
    }
    if (response.ok && method === "GET" && path.includes("/events")) return response;
    let text;
    try {
      text = await response.text();
    } catch (error) {
      if (error.name === "AbortError") throw error;
      // The status came, the answer didn't: what the service did is unknown, as if nothing had come.
      throw new NuruError(0, "unreachable", "Nuru's answer was cut off.");
    }
    const parsed = text ? JSON.parse(text) : {};
    if (!response.ok) {
      const error = parsed.error || {};
      const { code = "internal", message = `HTTP ${response.status}`, ...extra } = error;
      throw new NuruError(response.status, code, message, extra);
    }
    return parsed;
  }

  // A POST with request ID `requestId`, sent again with that same ID if no answer came back. A
  // NuruError with status 0 means it's unknown whether the service acted on it.
  async post(path, body, requestId = newRequestId()) {
    const payload = { request_id: requestId, ...body };
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await this.request("POST", path, payload);
      } catch (error) {
        if (!(error instanceof NuruError) || error.status !== 0 || attempt >= RETRIES) throw error;
      }
    }
  }

  // Protocol v2's status (the vault by its UUID); everything else is v1's, which a v2 service also serves.
  status() { return this.request("GET", "/v2/status"); }

  conversations(limit = 50) { return this.request("GET", `/v1/conversations?limit=${limit}`); }

  conversation(id, limit = 50) { return this.request("GET", `/v1/conversations/${encodeURIComponent(id)}?limit=${limit}`); }

  createConversation(title, requestId) { return this.post("/v1/conversations", title ? { title } : {}, requestId); }

  // `choice` ({model, thinking}, either optional) changes the conversation's model or thinking level
  // from this turn on; without it the turn runs on what the conversation last ran on.
  submit(conversationId, text, attachments = [], requestId, choice = {}) {
    return this.post(`/v1/conversations/${encodeURIComponent(conversationId)}/turns`,
      { text, ...(attachments.length ? { attachments } : {}), ...choice }, requestId);
  }

  // What became of a request (GET /v1/requests/{id}); a NuruError with status 404 if the service never
  // accepted it.
  requestState(requestId, operation) {
    return this.request("GET", `/v1/requests/${encodeURIComponent(requestId)}?operation=${operation}`);
  }

  cancel(turnId) { return this.post(`/v1/turns/${encodeURIComponent(turnId)}/cancel`, {}); }

  answer(questionId, operation, chosen, other) {
    return this.post(`/v1/questions/${encodeURIComponent(questionId)}/answers`,
      { operation, chosen, ...(other ? { other } : {}) });
  }

  // Reads the turn's events after `after`, calling onEvent for each, until the stream ends (after the
  // terminal event, or when the service closes it). Aborting `signal` stops reading.
  async events(turnId, after, onEvent, signal) {
    const response = await this.request("GET", `/v1/turns/${encodeURIComponent(turnId)}/events?after=${after}`,
      null, { signal });
    await readEvents(response.body, onEvent);
  }
}

module.exports = { NuruClient, NuruError };
