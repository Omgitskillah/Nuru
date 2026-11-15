"""F40 per-model choices outside the install. Atomic publication with revision conflicts.

Calibration is runtime evidence, not user input: a choice records its exact identity and
geometry and may only be used after comparing them with the current runner. No default
choice or model load is created by reading. Corrupt/newer-format data is never overwritten.
"""
import hashlib
import json
import re
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from core import user_models
from core.context_memory import Identity, Geometry, GB, Unsupported, _integer
from core.vault_identity import _exclusive

INITIAL = "initial"
MAX_BYTES = 4096
_REVISION = re.compile(r"[0-9a-f]{32}")


class Conflict(ValueError):
    """Another window saved first; reread before applying."""


@dataclass(frozen=True)
class Choice:
    model: str
    revision: str = INITIAL
    gb: int | None = None
    tokens: int | None = None
    identity: Identity | None = None
    layout: Geometry | None = None

    def usable(self, identity: Identity, layout: Geometry) -> bool:
        return self.tokens is not None and self.identity == identity and self.layout == layout


def directory() -> Path:
    return user_models.models_dir().parent / "context-memory"


def _path(model: str, root: Path) -> Path:
    user_models.check_name(model)
    if root.is_symlink():
        raise Unsupported("Context settings directory cannot be a symbolic link")
    path = root / (hashlib.sha256(model.encode("utf-8")).hexdigest() + ".json")
    if path.is_symlink():
        raise Unsupported("Context settings cannot be a symbolic link")
    return path


def _decode(data: dict, model: str) -> Choice:
    try:
        if set(data) != {"format", "model", "revision", "gb", "tokens", "identity", "layout"} \
                or type(data["format"]) is not int or data["format"] != 1 or data["model"] != model \
                or not isinstance(data["revision"], str) or not _REVISION.fullmatch(data["revision"]):
            raise ValueError("Invalid context settings format")
        gb, tokens = data["gb"], data["tokens"]
        if gb is None and tokens is None and data["identity"] is None and data["layout"] is None:
            return Choice(model, data["revision"])
        _integer(gb, "saved context memory", 4096)
        _integer(tokens, "saved context window")
        identity, layout = Identity(**data["identity"]), Geometry(**data["layout"])
        if identity.kv_type != "f16" or layout.architecture not in ("llama", "qwen2", "qwen3", "qwen35", "qwen35moe"):
            raise ValueError("Unsupported saved calibration")
        _integer(layout.window, "saved model window")
        _integer(layout.attention_layers, "saved attention layers", 1024)
        _integer(layout.bytes_per_token, "saved cache size", 1 << 30)
        if tokens > layout.window or tokens * layout.bytes_per_token * 95 > gb * GB * 100:
            raise ValueError("Saved window exceeds the model maximum")
        return Choice(model, data["revision"], gb, tokens, identity, layout)
    except (TypeError, KeyError, ValueError) as error:
        raise Unsupported("Context settings are invalid; preserve the file and repair it") from error


def read(model: str, root: Path | None = None) -> Choice:
    path = _path(model, root if root is not None else directory())
    try:
        with path.open("rb") as file:
            raw = file.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return Choice(model)
    if len(raw) > MAX_BYTES:
        raise Unsupported("Context settings are too large; preserve the file")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise Unsupported("Context settings are unreadable; preserve the file") from error
    if not isinstance(data, dict):
        raise Unsupported("Context settings must be an object; preserve the file")
    return _decode(data, model)


def publish(model: str, expected: str, *, gb: int | None, tokens: int | None,
            identity: Identity | None, layout: Geometry | None, root: Path | None = None) -> Choice:
    """Publish only after the caller successfully applies a verified window, or reset all fields.

    This CAS does not coordinate the model reload: the service must own its reload reservation
    throughout read/apply/publish, reject stale revisions before loading, and report uncertainty
    if publication fails after the runner changes. Readers always see a complete old/new choice.
    """
    root = root if root is not None else directory()
    path = _path(model, root)
    data = {"format": 1, "model": model, "revision": secrets.token_hex(16), "gb": gb, "tokens": tokens,
            "identity": asdict(identity) if identity is not None else None,
            "layout": asdict(layout) if layout is not None else None}
    choice = _decode(data, model)
    with _exclusive(root / "context.lock", busy=Conflict("Another Nuru is saving context settings")):
        if read(model, root).revision != expected:
            raise Conflict("Context settings changed; reopen the panel")
        user_models._write(path, json.dumps(data, ensure_ascii=False))
    return choice
