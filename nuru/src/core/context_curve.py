"""F40 effective runner allocation, measured without naming KV precision or parallelism.

Geometry is a plausibility gate. The fit includes compute-buffer growth; its intercept
is fitted, never substituted with model-file weight size. No conversion exceeds a window
that was actually loaded and checked. All arithmetic uses exact rational values.
"""
import math
from dataclasses import dataclass
from fractions import Fraction

from core.context_memory import GB, Geometry, Sample, Unsupported, _integer


@dataclass(frozen=True)
class Curve:
    geometry: Geometry
    slope: Fraction
    intercept: Fraction
    maximum: int

    def allocation(self, window: int) -> Fraction:
        return self.slope * window

    def agrees(self, sample: Sample) -> bool:
        if type(sample.window) is not int or not 8192 <= sample.window <= self.maximum:
            return False
        if type(sample.size) is not int or sample.size <= 0:
            return False
        return abs(sample.size - self.intercept - self.allocation(sample.window)) <= max(96_000_000, self.allocation(sample.window) / 10)

    def tokens(self, gb: int, *, minimum: int, admin_limit: int | None = None) -> int:
        _integer(gb, "context memory", 4096)
        _integer(minimum, "minimum context")
        ceiling = self.maximum if admin_limit is None else min(self.maximum, _integer(admin_limit, "admin limit"))
        result = min(ceiling, math.floor(gb * GB / self.slope))
        if result < minimum:
            raise Unsupported("Context memory is below the required minimum")
        return result

    def limits(self, minimum: int, admin_limit: int | None = None) -> tuple[int, int]:
        ceiling = self.maximum if admin_limit is None else min(self.maximum, _integer(admin_limit, "admin limit"))
        if minimum > ceiling:
            raise Unsupported("The verified maximum is below Nuru's required minimum")
        return max(1, math.ceil(minimum * self.slope / GB)), max(1, math.ceil(ceiling * self.slope / GB))


def fit(layout: Geometry, low: Sample, high: Sample, verified: Sample) -> Curve:
    for point in (low, high, verified):
        _integer(point.window, "actual measured context", layout.window)
        _integer(point.size, "resident model size", 1 << 50)
    if low.window != 8192 or high.window != 16384 or verified.window < high.window or high.size <= low.size:
        raise Unsupported("Calibration requires actual 8k, 16k and bounded verification loads")
    slope = Fraction(high.size - low.size, high.window - low.window)
    ratio = slope / layout.bytes_per_token
    # Products are only plausibility ranges, not assertions of the hidden server config.
    if not any(Fraction(9, 10) * precision * slots <= ratio <= Fraction(5, 4) * precision * slots
               for precision in (Fraction(1), Fraction(53, 100), Fraction(28, 100)) for slots in (1, 2, 4)):
        raise Unsupported("Measured allocation is not plausible for the supported model layout")
    intercept = low.size - slope * low.window
    if intercept <= 0:
        raise Unsupported("Invalid fitted runner baseline")
    curve = Curve(layout, slope, intercept, verified.window)
    if not curve.agrees(verified):
        raise Unsupported("The candidate allocation disagrees with the bounded calibration")
    # The absolute tolerance must still distinguish halving the context allocation at
    # the reference window. Otherwise it could certify a changed runner as unchanged.
    if curve.allocation(16384) / 2 <= 96_000_000:
        raise Unsupported("Context allocation is too small to verify reliably")
    return curve


def candidate(curve: Curve, *, total: int, available: int, maximum: int, minimum: int) -> int:
    """A conservative proposal, never a claim that it fits. Missing signals must refuse.

    Apply 0.66 to physical RAM once. Free/available memory provides a separate lower
    ceiling, with 20% left to other apps, plus 1.5GB explicit headroom after the baseline.
    No other resident model is assumed reclaimable.
    """
    _integer(total, "physical memory", 1 << 50)
    _integer(available, "available memory", total)
    _integer(maximum, "model/admin maximum")
    budget = min(Fraction(total * 66, 100), Fraction(available * 8, 10))
    proposed = math.floor((budget - curve.intercept - 1_500_000_000) / curve.slope)
    proposed = min(maximum, proposed)
    if proposed < minimum:
        raise Unsupported("Not enough available memory for the required context window")
    return proposed // 1024 * 1024
