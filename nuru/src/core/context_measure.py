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
    total, available = runner.available()  # missing signal refuses before any load
    before = runner.resident(model)  # never evict another model
    previous = None if before is None else _integer(before.get("context_length"), "previous loaded context")
    metadata, digest, version, fingerprint = runner.metadata(model)
    available = min(total, available + runner.reclaimable(before))
    budget = min(Fraction(total * 66, 100), Fraction(available * 8, 10))
    # The first probes must be affordable too. An absent resident baseline requires a
    # trusted model-size signal from metadata, not a guess that weights fit.
    if before is None:
        weight_size = metadata.get("size")
        _integer(weight_size, "model weight-size estimate", 1 << 50)
        baseline = weight_size
    else:
        baseline = _integer(before.get("size"), "previous resident size", 1 << 50)
    if baseline + layout.bytes_per_token * 16384 * 5 + 1_500_000_000 > budget:
        raise Unsupported("Not enough verified available memory for the bounded calibration probes")
    changed = False
    result = None
    try:
        points = []
        placement = None
        for step, window in enumerate((8192, 16384), 1):
            now_total, now_available = runner.available()
            now_available = min(now_total, now_available + runner.reclaimable(runner.resident(model)))
            now_budget = min(Fraction(now_total * 66, 100), Fraction(now_available * 8, 10))
            if baseline + layout.bytes_per_token * window * 5 + 1_500_000_000 > now_budget:
                raise Unsupported("Available memory changed; measurement stopped before another load")
            progress(step, 4); changed = True
            point, location = runner.cold_load(model, window)
            if placement is not None and location != placement:
                raise Unsupported("Model placement changed during measurement")
            placement = location; points.append(point)
            if runner.metadata(model)[1:] != (digest, version, fingerprint):
                raise Unsupported("Model or runner configuration changed during measurement")
        provisional = fit(layout, points[0], points[1], points[1])
        now_total, now_available = runner.available()
        now_available = min(now_total, now_available + runner.reclaimable(runner.resident(model)))
        proposed = candidate(provisional, total=now_total, available=now_available, maximum=maximum, minimum=minimum)
        verified = provisional
        verified_point = points[1]
        for attempt in range(2):
            now_total, now_available = runner.available()
            now_available = min(now_total, now_available + runner.reclaimable(runner.resident(model)))
            proposed = min(proposed, candidate(provisional, total=now_total, available=now_available, maximum=maximum, minimum=minimum))
            if proposed <= 16384:
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
                proposed = (16384 + (proposed - 16384) * 3 // 4) // 1024 * 1024
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
