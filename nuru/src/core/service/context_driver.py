"""F40 production adapter: no loads on reads; measurement/Apply only under explicit PUT."""
import math
import os
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

from core import context_apply, context_preferences as preferences, context_records
from core.context_budget import available_tokens
from core.context_lease import ComputeLease
from core.context_measure import measure
from core.context_memory import Identity, Unsupported, geometry, _integer
from core.context_runner import Uncertain


class ContextDriver:
    def __init__(self, service, runner, default_window, *, root=None, lease=ComputeLease):
        self.service, self.runner, self.default_window = service, runner, default_window
        self.root, self.lease = root, lease
        self.live = {}
        self.progress = None
        self.owns_compute = False
        self.live_keys = {}
        self.pending_loads = set()

    def _minimum(self, model):
        s = self.service._session
        if s is None or s.model_id() != model:
            raise Unsupported("Send a message with this model first, so Nuru can check its prompt and tools")
        # Actual sent system/tools, plus two bounded 4k read results and output reserve.
        overhead = 32768 - 2048 - available_tokens(32768, [], system=s._system_text(), tools=s.tool_definitions)
        return max(16384, math.ceil((overhead + 8192 + 2048) / 1024) * 1024)

    @staticmethod
    def _admin():
        value = os.environ.get("NURU_MAX_NUM_CTX")
        if value is None: return None
        if not value.isdecimal() or not 0 < int(value) <= 16777216:
            raise Unsupported("NURU_MAX_NUM_CTX is not a valid context limit")
        return int(value)

    def _evidence(self, model):
        shown, digest, version, fingerprint = self.runner.metadata(model)
        layout = geometry(shown, "f16")  # reference geometry only; no claim about runner precision
        record = context_records.read(model, self.root)
        if record is not None and (record.digest, record.server_version, record.fingerprint, record.curve.geometry) != (digest, version, fingerprint, layout):
            record = None
        return shown, layout, record

    def describe(self, model):
        saved = preferences.read(model, self.root)
        shown, layout, record = self._evidence(model)
        window = self.live.get(model, saved.tokens or self.default_window({"context_window": layout.window}))
        result = {"model": model, "revision": saved.revision, "window_tokens": window,
                  "measured": False, "can_measure": False, "busy": self.service._model_changing}
        try:
            minimum = self._minimum(model)
            if self.compute_blocked():
                raise Unsupported("Local compute is reserved or uncertain; stop the owning service, restart Ollama, then use nuru-serve compute check for recovery")
            resident = self.runner.resident(model)  # no load, and no implicit eviction of another model
            if resident is not None:
                window = _integer(resident.get("context_length"), "actual context window")
                result["window_tokens"] = window
            self.runner.available()
            result["can_measure"] = True
        except (Unsupported, OSError) as error:
            result["reason"] = str(error)
            return result
        if record is None:
            result["reason"] = "Memory use has not been measured for this runner configuration"
            return result
        low, high = record.curve.limits(minimum, self._admin())
        gb = (None if model in self.live else saved.gb) or max(low, min(high, math.ceil(window * record.curve.slope / 1_000_000_000)))
        gb = max(low, min(high, gb))
        cap = min(record.curve.maximum, self._admin() or layout.window)
        reason = "This model's largest window" if cap == layout.window else "Largest verified on this computer"
        if self._admin() is not None and cap == self._admin(): reason = "Limited by NURU_MAX_NUM_CTX"
        if record.placement != "gpu": reason += " (partly on CPU: slower)" if record.placement == "mixed" else " (CPU: slower)"
        result.update(measured=True, gb=gb, min_gb=low, max_gb=high, max_tokens=cap,
                      bytes_per_token=float(record.curve.slope), limit_reason=reason,
                      needs_apply=saved.gb is None or window != record.curve.tokens(gb, minimum=minimum, admin_limit=self._admin()))
        result.pop("reason", None)
        return result

    def measure(self, model):
        minimum = self._minimum(model)
        shown, layout, _ = self._evidence(model)
        with self.lease(model) as lease:
            self.owns_compute = True
            try:
                result = measure(self.runner, model, layout, minimum=minimum, admin_limit=self._admin(), lease=lease,
                                 progress=lambda step, total: setattr(self, "progress", (step, total)))
                context_records.publish(model, result, self.root)
            finally:
                self.progress = None
                self.owns_compute = False
        response = self.describe(model); response["busy"] = False
        return response

    def apply(self, model, gb, expected):
        minimum = self._minimum(model)
        _, layout, record = self._evidence(model)
        if record is None: raise Unsupported("Memory use changed: measure again")
        curve = record.curve
        low, high = curve.limits(minimum, self._admin())
        if type(gb) is not int or not low <= gb <= high:
            raise Unsupported("Choose a size within this model's verified limits")
        identity = Identity(record.digest, record.server_version, "measured:" + record.fingerprint)
        stored_layout = replace(layout, window=curve.maximum, bytes_per_token=math.ceil(curve.slope))
        calibration = SimpleNamespace(tokens=curve.tokens, identity=identity, geometry=stored_layout)
        def verify(window):
            point, placement = self.runner.verify(model, window)
            return curve.agrees(point) and self.runner.metadata(model)[1:] == (record.digest, record.server_version, record.fingerprint)
        with self.lease(model) as lease:
            self.owns_compute = True
            try:
                if self._evidence(model)[2] != record:
                    raise Unsupported("Model configuration changed; measure again")
                self._affordable(model, curve.tokens(gb, minimum=minimum, admin_limit=self._admin()), record)
                result = context_apply.apply(model, expected, gb, calibration, minimum=minimum,
                    reservation=nullcontext, live_window=lambda: self._previous_window(model, layout),
                    reload=lambda n: self.runner.load(model, n), verify=verify, set_live=lambda n: self._set_live(model, n, record),
                    root=self.root, admin_limit=self._admin())
            except (Uncertain, context_apply.ApplyFailed) as error:
                if isinstance(error, context_apply.ApplyFailed) and error.uncertain:
                    self.runner.uncertain = True
                if self.runner.uncertain:
                    lease.retain("The Apply load is uncertain; reconcile the runner before releasing ownership")
                raise
            finally:
                self.owns_compute = False
        response = self.describe(model); response["busy"] = False
        if result.notice: response["notice"] = result.notice
        point, location = self.runner.verify(model, result.window)
        if location != "gpu": response["notice"] = "Doesn't fully fit in GPU memory: replies will be slower." + (" " + result.notice if result.notice else "")
        return response

    def _set_live(self, model, window, record):
        self.live[model] = window
        self.live_keys[model] = (record.digest, record.server_version, record.fingerprint)

    def compute_blocked(self):
        if self.runner.uncertain: return True
        if self.owns_compute: return False
        try:
            self.lease("ownership-check").path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return True

    def _previous_window(self, model, layout):
        resident = self.runner.resident(model)
        return _integer(resident.get("context_length"), "current runtime window") if resident is not None else self.default_window({"context_window": layout.window})

    def _affordable(self, model, value, record, *, exclusive=True):
        row = self.runner.resident(model, exclusive=exclusive)
        if row is not None:
            point, _ = self.runner.verify(model, row["context_length"], exclusive=exclusive)
            if not record.curve.agrees(point): raise Unsupported("Memory use changed: measure again")
            # Reusing or shrinking an agreeing allocation needs no extra memory.
            # The reload is still verified against the curve before publication.
            if value <= point.window:
                return
        total, available = self.runner.available()
        available = min(total, available + self.runner.reclaimable(row))
        budget = min(total * 66 // 100, available * 8 // 10)
        # Identity was checked by the caller; use the measured curve for preflight.
        # Post-load verification still rejects hidden runner configuration changes.
        allocation = record.curve.intercept + record.curve.allocation(value)
        if allocation + 1_500_000_000 > budget:
            raise Unsupported("Not enough available memory for this context size; measure again or choose a smaller size")

    def window(self, model, info, default):
        if self.compute_blocked(): raise Unsupported("Local model compute is reserved or uncertain; stop the owning service, restart Ollama, then use nuru-serve compute check for recovery")
        saved = preferences.read(model, self.root)
        if model not in self.live and saved.tokens is None: return default
        _, layout, record = self._evidence(model)
        if record is None: raise Unsupported("Memory use changed: measure again before using the saved context size")
        if model in self.live and self.live_keys.get(model) != (record.digest, record.server_version, record.fingerprint):
            raise Unsupported("The runtime context choice belongs to another model configuration; apply again")
        if model not in self.live:
            identity = Identity(record.digest, record.server_version, "measured:" + record.fingerprint)
            stored_layout = replace(layout, window=record.curve.maximum, bytes_per_token=math.ceil(record.curve.slope))
            if not saved.usable(identity, stored_layout):
                raise Unsupported("Saved context belongs to another runner configuration; measure and apply again")
        value = self.live.get(model, saved.tokens)
        self._affordable(model, value, record, exclusive=False)
        if value > min(record.curve.maximum, self._admin() or layout.window):
            raise Unsupported("The saved context exceeds the current verified/admin limit; reopen the panel")
        return value

    def before_load(self, model):
        # Track only a model absent immediately before this attempt. Never unload a
        # pre-existing allocation merely because a later verification disagrees.
        self.pending_loads.discard(model)
        if self.runner.resident(model, exclusive=False) is None:
            self.pending_loads.add(model)

    def verify_loaded(self, model):
        newly_loaded = model in self.pending_loads
        self.pending_loads.discard(model)
        saved = preferences.read(model, self.root)
        if model not in self.live and saved.tokens is None: return
        try:
            _, _, record = self._evidence(model)
            if record is None: raise Unsupported("Memory use changed: measure again")
            value = self.live.get(model, saved.tokens)
            point, location = self.runner.verify(model, value, exclusive=False)
            if not record.curve.agrees(point):
                raise Unsupported("Memory use changed: measure again")
        except Unsupported:
            if newly_loaded:
                self.runner.unload_owned(model, exclusive=False)
            raise
