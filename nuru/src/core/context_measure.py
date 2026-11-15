"""F40 bounded measurement with mandatory restoration and explicit uncertainty.

The caller holds both the service reservation and a compute lease. A timeout, or failed
restoration, retains the lease: no success is reported and no cleanup guesses that the
runner stopped. Progress counts measurement attempts; restoration is always separate.
"""
from dataclasses import dataclass
from fractions import Fraction

from core.context_curve import Curve, fit, candidate
from core.context_memory import Sample, Unsupported, _integer
from core.context_runner import Uncertain


@dataclass(frozen=True)
class Measurement:
    curve: Curve
    digest: str
    server_version: str
    fingerprint: str
    placement: str
    samples: tuple[Sample, Sample, Sample]


def measure(runner, model, layout, *, minimum: int, lease, admin_limit=None, progress=lambda step, total: None):
    minimum = max(16384, _integer(minimum, "required context"))
    maximum = layout.window if admin_limit is None else min(layout.window, _integer(admin_limit, "admin context limit"))
    if maximum < minimum:
        raise Unsupported("The model/admin limit is below Nuru's required context window")
    runner.available()  # missing signal refuses before any load
    before = runner.resident(model)  # never evict another model
    previous = None if before is None else _integer(before.get("context_length"), "previous loaded context")
    metadata, digest, version, fingerprint = runner.metadata(model)
    weights = _integer(metadata.get("size"), "model weight-size estimate", 1 << 50)
    resident_size = None if before is None else _integer(before.get("size"), "previous resident size", 1 << 50)
    if before is not None and (type(before.get("size_vram")) is not int or not 0 <= before["size_vram"] <= resident_size):
        raise Unsupported("The previous model placement is unknown; measurement cannot safely restore it")
    changed = False
    result = None
    try:
        points = []
        placement = None
        for step, window in enumerate((8192, 16384), 1):
            now_total, now_available = runner.available()
            now_available = min(now_total, now_available + runner.reclaimable(runner.resident(model)))
            now_budget = min(Fraction(now_total * 66, 100), Fraction(now_available * 8, 10))
            if previous is not None and window <= previous:
                # This replaces a demonstrated larger allocation, rather than adding
                # a new cache on top of its resident size. Still stop on lost capacity.
                need = resident_size
                ceiling = min(Fraction(now_total * 66, 100), now_available)
            else:
                # Disk weight size is not a proven lower bound on resident weights.
                # Scaling the entire observed allocation bounds nonnegative baseline
                # plus linear cache growth without subtracting an unverified baseline.
                need = (Fraction(points[0].size * window, 8192) if points else weights + layout.bytes_per_token * window * 5) + 1_500_000_000
                ceiling = now_budget
            if need > ceiling:
                if previous is None:
                    raise Unsupported("Send a message with this model first, so Nuru can measure from its loaded state")
                raise Unsupported("Available memory changed; not enough verified available memory for calibration probes")
            progress(step, 4); changed = True
            point, location = runner.cold_load(model, window)
            if placement is not None and location != placement:
                raise Unsupported("Model placement changed during measurement")
            placement = location; points.append(point)
            if runner.metadata(model)[1:] != (digest, version, fingerprint):
                raise Unsupported("Model or runner configuration changed during measurement")
        provisional = fit(layout, points[0], points[1], points[1])
        verified = provisional
        verified_point = points[1]
        if previous is not None and 16384 < previous <= maximum and before.get("digest") == digest:
            vram = before.get("size_vram")
            if type(vram) is int and 0 <= vram <= resident_size:
                prior_placement = "gpu" if vram == resident_size else "cpu" if vram == 0 else "mixed"
                if prior_placement == placement:
                    try:
                        verified = fit(layout, points[0], points[1], Sample(previous, resident_size))
                        verified_point = Sample(previous, resident_size)
                    except Unsupported:
                        pass  # An old allocation that disagrees supplies no evidence.
        def propose(total, available):
            try:
                return candidate(provisional, total=total, available=available, maximum=maximum, minimum=minimum)
            except Unsupported:
                # A growth estimate cannot erase an agreeing, actually loaded sample.
                # Keep it only while current capacity still supports that allocation.
                if minimum <= verified.maximum and verified_point.size <= min(total * 66 // 100, available):
                    return verified.maximum
                raise
        now_total, now_available = runner.available()
        now_available = min(now_total, now_available + runner.reclaimable(runner.resident(model)))
        proposed = propose(now_total, now_available)
        for attempt in range(2):
            now_total, now_available = runner.available()
            now_available = min(now_total, now_available + runner.reclaimable(runner.resident(model)))
            proposed = min(proposed, propose(now_total, now_available))
            if proposed <= verified.maximum:
                break
            progress(3 + attempt, 4)
            point, location = runner.cold_load(model, proposed)
            if runner.metadata(model)[1:] != (digest, version, fingerprint):
                raise Unsupported("Runner configuration changed during measurement")
            try:
                if location != placement:
                    raise Unsupported("Candidate changed model placement")
                verified = fit(layout, points[0], points[1], point)
                verified_point = point
                break
            except Unsupported:
                proposed = (verified.maximum + (proposed - verified.maximum) * 3 // 4) // 1024 * 1024
        if verified.maximum < minimum:
            raise Unsupported("No sufficiently large window could be verified")
        result = Measurement(verified, digest, version, fingerprint, placement, (points[0], points[1], verified_point))
    finally:
        if changed:
            if runner.uncertain:
                lease.retain("A model load is uncertain; reconciliation is required before releasing ownership")
                raise Uncertain("Measurement did not finish: a load is uncertain and compute ownership was retained")
            try:
                if previous is None:
                    runner.unload_owned(model)
                else:
                    restored, location = runner.cold_load(model, previous)
                    original_location = "gpu" if before.get("size_vram") == before.get("size") else "cpu" if before.get("size_vram") == 0 else "mixed"
                    if restored.window != previous or location != original_location:
                        raise Unsupported("The previous model state was not restored")
                if runner.metadata(model)[1:] != (digest, version, fingerprint):
                    raise Unsupported("The previous model identity could not be restored")
            except Exception as error:
                runner.uncertain = True
                lease.retain("The previous model state could not be restored")
                raise Uncertain("Measurement did not finish: restoration failed and compute ownership was retained") from error
    return result
