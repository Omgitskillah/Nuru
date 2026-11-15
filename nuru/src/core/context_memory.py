"""F40's model-free sizing checks. No model loads and no inferred server KV precision.

Only uniform f16 full attention and explicitly described Qwen 3.5 hybrids are understood.
A geometry prediction alone never enables resizing: bounded, isolated runner measurements
must agree first. Memory here is the variable context allocation, not total model RAM.
"""
from dataclasses import dataclass
from fractions import Fraction

GB = 1_000_000_000
MAX_WINDOW = 16_777_216


class Unsupported(ValueError):
    """Not enough trustworthy information to offer a context-memory conversion."""


def _integer(value, name: str, maximum: int = MAX_WINDOW) -> int:
    if type(value) is not int or not 0 < value <= maximum:
        raise Unsupported(f"Invalid {name}")
    return value


@dataclass(frozen=True)
class Geometry:
    architecture: str
    window: int
    attention_layers: int
    bytes_per_token: int


def geometry(shown: dict, kv_type: str) -> Geometry:
    """Use explicit /api/show metadata; kv_type must come from the actual runner configuration.

    Qwen 3.5 uses full attention at (layer + 1) % interval == 0 unless explicit recurrent
    flags override it (llama.cpp src/models/qwen35.cpp). No default interval is guessed.
    """
    if kv_type != "f16":
        raise Unsupported("Unsupported or unknown runner KV cache type")
    info = shown.get("model_info")
    if not isinstance(info, dict):
        raise Unsupported("Missing model metadata")
    arch = info.get("general.architecture")
    if arch not in ("llama", "qwen2", "qwen3", "qwen35", "qwen35moe", "granite"):
        raise Unsupported("Unsupported context-memory architecture")
    prefix = arch + "."
    if any("sliding_window" in k or "shared_kv" in k or "nextn_predict" in k for k in info):
        raise Unsupported("Unsupported sliding, shared-KV or prediction-layer layout")
    def field(name):
        return _integer(info.get(prefix + name), name)
    layers = field("block_count")
    if layers > 1024:
        raise Unsupported("Unsupported layer count")
    if arch in ("qwen35", "qwen35moe"):
        flags = info.get(prefix + "attention.recurrent_layers")
        if flags is not None:
            if not isinstance(flags, list) or len(flags) != layers or any(type(x) is not bool for x in flags):
                raise Unsupported("Invalid recurrent-layer flags")
            layers = flags.count(False)
        else:
            interval = field("full_attention_interval")
            layers //= interval
        if not layers:
            raise Unsupported("No full-attention layers")
    heads = field("attention.head_count_kv")
    if arch == "granite" and prefix + "attention.key_length" not in info and prefix + "attention.value_length" not in info:
        embedding, query_heads = field("embedding_length"), field("attention.head_count")
        if embedding % query_heads:
            raise Unsupported("Granite head dimensions are not uniform")
        key = value = embedding // query_heads
    else:
        key, value = field("attention.key_length"), field("attention.value_length")
    window = field("context_length")
    return Geometry(arch, window, layers, layers * heads * (key + value) * 2)


@dataclass(frozen=True)
class Identity:
    digest: str
    server_version: str
    kv_type: str

    def __post_init__(self):
        for value in (self.digest, self.server_version, self.kv_type):
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise Unsupported("Missing calibration identity")


@dataclass(frozen=True)
class Sample:
    window: int
    size: int


@dataclass(frozen=True)
class Calibration:
    identity: Identity
    geometry: Geometry
    bytes_per_token: Fraction

    def tokens(self, gb: int, *, minimum: int, admin_limit: int | None = None) -> int:
        """Whole decimal GB steps, rounded down to tokens and bounded by verified limits."""
        _integer(gb, "context memory", 4096)
        _integer(minimum, "minimum context")
        ceiling = self.geometry.window
        if admin_limit is not None:
            ceiling = min(ceiling, _integer(admin_limit, "admin context limit"))
        tokens = min(ceiling, int(gb * GB / self.bytes_per_token))
        if tokens < minimum:
            raise Unsupported("Context memory is below the required minimum")
        return tokens

    def matches(self, identity: Identity, layout: Geometry) -> bool:
        return self.identity == identity and self.geometry == layout


def calibrate(identity: Identity, layout: Geometry, low: Sample, high: Sample) -> Calibration:
    """Accept two bounded isolated /api/ps measurements only when the KV prediction agrees.

    Caller must verify the same digest, server, cache type, parallelism and placement for
    both samples, no other activity, and the requested windows actually loaded. Runtime
    adapters must not create these samples from aggregate GPU memory or requested sizes.
    """
    if identity.kv_type != "f16":
        raise Unsupported("Calibration precision does not match supported geometry")
    for sample in (low, high):
        _integer(sample.window, "measured context window", min(layout.window, 16384))
        _integer(sample.size, "resident bytes", 1 << 50)
    if high.window - low.window < 4096 or high.size <= low.size:
        raise Unsupported("Calibration needs distinct, increasing bounded samples")
    slope = Fraction(high.size - low.size, high.window - low.window)
    # Five percent allows allocation alignment, not a different cache/layout or placement.
    if abs(slope - layout.bytes_per_token) > Fraction(layout.bytes_per_token, 20):
        raise Unsupported("Measured memory disagrees with model metadata")
    if low.size < slope * low.window:
        raise Unsupported("Impossible resident-memory baseline")
    return Calibration(identity, layout, slope)
