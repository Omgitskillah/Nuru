"""F40's Apply transaction, with injected runner operations for model-free failure tests.

The adapter owns the service reservation and actual model identity/calibration checks.
`live_window` reports the current runtime window (including a prior unsaved application),
not merely the stored preference. Nothing here infers residency from a requested window.
"""
from dataclasses import dataclass

from core import context_preferences as preferences
from core.context_memory import Calibration, Unsupported, _integer


class ApplyFailed(RuntimeError):
    """The new window was not applied. `uncertain` means rollback could not be verified."""

    def __init__(self, uncertain: bool):
        self.uncertain = uncertain
        super().__init__("Couldn't change context memory; the previous window couldn't be restored. "
                         "Check the model before sending another message." if uncertain else
                         "Couldn't change context memory; the previous window was restored.")


@dataclass(frozen=True)
class Applied:
    window: int
    saved: bool
    notice: str | None = None


def apply(model: str, expected: str, gb: int, calibration: Calibration, *, minimum: int,
          reservation, live_window, reload, verify, set_live, root=None,
          admin_limit: int | None = None, publish=None) -> Applied:
    """Apply once under `reservation()`; publish only after verifying the runner.

    `verify(window)` must return exactly True after checking actual loaded context and
    memory/identity; unknown residency is failure. `set_live(window)` updates the service's
    per-model runtime choice for all subsequent turns/titles, without another model load.
    Reload and verification failures attempt rollback; persistence failure keeps the verified
    new window active, with an explicit unsaved result. Exceptions never claim a saved change.
    """
    window = calibration.tokens(gb, minimum=minimum, admin_limit=admin_limit)
    publish = publish or preferences.publish
    with reservation():
        old = preferences.read(model, root)
        if old.revision != expected:
            raise preferences.Conflict("Context settings changed; reopen the panel")
        previous = _integer(live_window(), "current runtime window")
        try:
            reload(window)
            if verify(window) is not True:
                raise Unsupported("The model's new window could not be verified")
            set_live(window)
        except Exception as error:
            try:
                reload(previous)
                if verify(previous) is not True:
                    raise Unsupported("The previous window could not be verified")
                set_live(previous)
            except Exception:
                raise ApplyFailed(uncertain=True) from error
            raise ApplyFailed(uncertain=False) from error
        try:
            publish(model, expected, gb=gb, tokens=window, identity=calibration.identity,
                    layout=calibration.geometry, root=root)
        except (OSError, ValueError):
            # A verified runtime change must not be presented as persisted. The adapter shows
            # this notice and keeps its runtime override until restart or another Apply.
            return Applied(window, False, "Applied for now, but couldn't be saved. "
                           "Saved settings will apply after a restart.")
        return Applied(window, True)
