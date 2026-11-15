"""Bounded Ollama operations for F40. No loads on construction or metadata reads.

The orchestration layer must hold compute ownership and the service reservation. Loads
refuse other resident models, request no generated answer, and verify actual context and
residency. A timeout is an uncertain server operation, never evidence that it stopped.
"""
import hashlib
import json
import platform
import sys

import psutil
import requests

from core.context_memory import Sample, Unsupported, _integer
from core.providers import ollama


class Uncertain(RuntimeError):
    """The server may still be loading; ownership must not be released automatically."""


class Runner:
    def __init__(self, alias, *, http=requests, base=None, memory=psutil.virtual_memory):
        self.alias, self.http = alias, http
        self.base = base or ollama.BASE
        self.memory = memory
        self.uncertain = False

    def _get(self, path):
        response = self.http.get(self.base + path, timeout=10)
        response.raise_for_status()
        return response.json()

    def resident(self, model):
        target = ollama._normalize_model_name(self.alias(model))
        rows = self._get("/api/ps").get("models")
        if not isinstance(rows, list):
            raise Unsupported("Ollama did not report resident models")
        own = None
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                raise Unsupported("Ollama reported invalid model residency")
            if ollama._normalize_model_name(row["name"]) != target:
                raise Unsupported("Another model is resident; memory measurement will not unload it")
            if own is not None:
                raise Unsupported("Ollama reported ambiguous model residency")
            own = row
        return own

    def metadata(self, model):
        response = self.http.post(self.base + "/api/show", json={"model": self.alias(model)}, timeout=10)
        response.raise_for_status()
        shown = response.json()
        version = self._get("/api/version").get("version")
        tags = self._get("/api/tags").get("models", [])
        target = ollama._normalize_model_name(self.alias(model))
        matches = [r for r in tags if isinstance(r, dict) and isinstance(r.get("name"), str)
                   and ollama._normalize_model_name(r["name"]) == target]
        if len(matches) != 1 or not isinstance(version, str) or not version:
            raise Unsupported("Could not identify this model and Ollama version")
        digest = matches[0].get("digest")
        if not isinstance(digest, str) or not digest or not isinstance(shown, dict):
            raise Unsupported("Could not identify the model metadata")
        # This fingerprints observable configuration, not guessed cache precision/slots.
        fingerprint = hashlib.sha256(json.dumps({"digest": digest, "version": version,
            "info": shown.get("model_info"), "parameters": shown.get("parameters"),
            "system": shown.get("system"), "template": shown.get("template")}, sort_keys=True).encode()).hexdigest()
        return {**shown, "size": matches[0].get("size")}, digest, version, fingerprint

    def available(self):
        memory = self.memory()
        total = _integer(memory.total, "physical memory", 1 << 50)
        available = _integer(memory.available, "available memory", total)
        return total, available

    def reclaimable(self, row):
        # CPU allocations and Apple Silicon unified memory occupy the RAM reported by
        # psutil. Dedicated VRAM must not be added to an unrelated free-RAM signal.
        unified = sys.platform == "darwin" and platform.machine() == "arm64"
        return row["size"] if row is not None and (row.get("size_vram") == 0 or unified) else 0

    def load(self, model, window):
        """One bounded load, followed by an actual residency check; no automatic retries."""
        _integer(window, "requested context")
        self.resident(model)  # refuse other models before any load request
        if self.uncertain:
            raise Uncertain("A previous load is still uncertain; reconcile it before another load")
        try:
            response = self.http.post(self.base + "/api/generate", json={"model": self.alias(model),
                "options": {"num_ctx": window}, "keep_alive": "30m", "stream": False}, timeout=30)
            response.raise_for_status()
        except requests.RequestException as error:
            self.uncertain = True
            raise Uncertain("The load did not complete with a verified response; the model may still be loading") from error
        return self.verify(model, window)

    def verify(self, model, window):
        row = self.resident(model)
        if row is None or type(row.get("context_length")) is not int or row["context_length"] != window:
            raise Unsupported("Ollama did not confirm the requested context window")
        size = _integer(row.get("size"), "resident bytes", 1 << 50)
        vram = row.get("size_vram")
        if type(vram) is not int or not 0 <= vram <= size:
            raise Unsupported("Ollama did not report usable CPU/GPU residency")
        return Sample(window, size), ("gpu" if vram == size else "cpu" if vram == 0 else "mixed")

    def cold_load(self, model, window):
        self.unload_owned(model)
        return self.load(model, window)

    def unload_owned(self, model):
        """Only the reserved target may be unloaded for an explicit measurement or restoration."""
        self.resident(model)
        if self.uncertain:
            raise Uncertain("An uncertain load must be reconciled before restoration")
        try:
            response = self.http.post(self.base + "/api/generate", json={"model": self.alias(model), "keep_alive": 0}, timeout=30)
            response.raise_for_status()
        except requests.RequestException as error:
            self.uncertain = True
            raise Uncertain("Model restoration is uncertain") from error
        if self.resident(model) is not None:
            raise Unsupported("The initially-unloaded model could not be restored to that state")
