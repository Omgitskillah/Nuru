"""Bounded calibration evidence outside the install; reconstruct the fit on every read."""
import json
import re
from dataclasses import asdict

from core import context_preferences as preferences
from core.context_curve import fit
from core.context_memory import Geometry, Sample, Unsupported, _integer
from core.context_measure import Measurement


def _path(model, root):
    path = preferences._path(model, root if root is not None else preferences.directory())
    path = path.with_suffix(".calibration.json")
    if path.is_symlink():
        raise Unsupported("Calibration evidence cannot be a symbolic link")
    return path


def read(model, root=None):
    path = _path(model, root)
    try:
        with path.open("rb") as file: raw = file.read(8193)
    except FileNotFoundError:
        return None
    try:
        if len(raw) > 8192: raise ValueError("too large")
        data = json.loads(raw)
        if set(data) != {"format", "model", "digest", "server_version", "fingerprint", "placement", "layout", "samples"} or type(data["format"]) is not int or data["format"] != 1 or data["model"] != model:
            raise ValueError("invalid shape")
        if any(not isinstance(data[k], str) or not data[k] or len(data[k]) > 256 for k in ("digest", "server_version")) or not isinstance(data["fingerprint"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["fingerprint"]) or data["placement"] not in ("cpu", "gpu", "mixed"):
            raise ValueError("invalid identity")
        layout = Geometry(**data["layout"])
        if layout.architecture not in ("llama", "qwen2", "qwen3", "qwen35", "qwen35moe", "granite"):
            raise ValueError("unsupported architecture")
        _integer(layout.window, "architecture window")
        _integer(layout.attention_layers, "attention layers", 1024)
        _integer(layout.bytes_per_token, "cache geometry", 1 << 30)
        if not isinstance(data["samples"], list) or len(data["samples"]) != 3:
            raise ValueError("three samples required")
        samples = tuple(Sample(**s) for s in data["samples"])
        curve = fit(layout, *samples)
        return Measurement(curve, data["digest"], data["server_version"], data["fingerprint"], data["placement"], samples)
    except (ValueError, TypeError, KeyError) as error:
        raise Unsupported("Calibration evidence is invalid; preserve it and measure again after repair") from error


def publish(model, result, root=None):
    path = _path(model, root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Never replace an unreadable/newer record under the assumption it is ours.
    read(model, root)
    data = {"format": 1, "model": model, "digest": result.digest, "server_version": result.server_version,
            "fingerprint": result.fingerprint, "placement": result.placement,
            "layout": asdict(result.curve.geometry), "samples": [asdict(s) for s in result.samples]}
    preferences.durable_write(path, json.dumps(data))
