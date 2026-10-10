// A client of nuru-serve's protocol v1 over fetch. Every POST carries a request ID; when its response
// is lost (the connection drops), it is sent again with the same ID, which the service answers with
// the result it already gave instead of acting twice.
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
  constructor({ baseUrl, token, fetch = globalThis.fetch }) {
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
      throw new NuruError(0, "unreachable", "Nuru isn't answering. Is nuru-serve running?");
    }
    if (response.ok && method === "GET" && path.includes("/events")) return response;
    const text = await response.text();
    const parsed = text ? JSON.parse(text) : {};
    if (!response.ok) {
      const error = parsed.error || {};
      const { code = "internal", message = `HTTP ${response.status}`, ...extra } = error;
      throw new NuruError(response.status, code, message, extra);
    }
    return parsed;
  }

  // A POST with a fresh request ID, sent again with that same ID if no answer came back.
  async post(path, body) {
    const payload = { request_id: newRequestId(), ...body };
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await this.request("POST", path, payload);
      } catch (error) {
        if (!(error instanceof NuruError) || error.status !== 0 || attempt >= RETRIES) throw error;
      }
    }
  }

  status() { return this.request("GET", "/v1/status"); }

  conversations(limit = 50) { return this.request("GET", `/v1/conversations?limit=${limit}`); }

  conversation(id, limit = 50) { return this.request("GET", `/v1/conversations/${encodeURIComponent(id)}?limit=${limit}`); }

  createConversation(title) { return this.post("/v1/conversations", title ? { title } : {}); }

  submit(conversationId, text, attachments = []) {
    return this.post(`/v1/conversations/${encodeURIComponent(conversationId)}/turns`,
      { text, ...(attachments.length ? { attachments } : {}) });
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
