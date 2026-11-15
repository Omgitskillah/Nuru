"""F40 local administration contract and admission, independent of the Ollama adapter.

GET never measures. PUT is one explicit attempt and never retries model work. The driver
must reconcile timeouts before returning and preserve other models' residency. Both reads
and writes validate their public shape so runtime details/paths cannot leak to the UI.
"""
import json
from functools import cache
from pathlib import Path

import jsonschema

from core import context_apply, context_recovery, user_models
from core.providers import claude
from core.context_memory import Unsupported
from core.context_runner import Uncertain
from core.context_preferences import Conflict
from core.service.protocol import ProtocolError, _problem

MAX_BYTES = 4096


@cache
def _validator(kind):
    schema = json.loads(Path(__file__).with_name("context-settings-v1.schema.json").read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator({**schema, "$ref": f"#/$defs/{kind}"})


def validate(kind, value):
    error = jsonschema.exceptions.best_match(_validator(kind).iter_errors(value))
    if error is not None:
        raise ProtocolError("invalid_request", _problem(error)[:500])


class ContextSettings:
    def __init__(self, service, driver):
        self.service, self.driver = service, driver

    def _model(self, model):
        # Choice lookup is metadata only. A GET may not construct Session, build an alias,
        # start Ollama, or load/unload any model.
        models = self.service.status().get("models") or {}
        choice = next((c for c in models.get("choices", []) if c["id"] == model), None)
        if choice is None:
            raise ProtocolError("not_found", "This model is not available; refresh the model list")
        return choice

    def describe(self, model):
        choice = self._model(model)
        if choice.get("cloud"):
            result = {"model": model, "revision": "initial", "window_tokens": claude.model_info(claude.model_id(model))["context_window"],
                      "measured": False, "can_measure": False, "busy": False,
                      "reason": "Claude's window is set by Anthropic"}
        else:
            try:
                user_models.check_name(model)
                result = self.driver.describe(model)
            except (ValueError, OSError) as error:
                raise ProtocolError("model_unavailable", str(error)[:500]) from None
        validate("settings", result)
        if result["model"] != model:
            raise ProtocolError("invalid_request", "Context settings belong to a different model")
        return result

    def change(self, body):
        validate("change", body)
        model = body["model"]
        choice = self._model(model)
        if choice.get("cloud"):
            raise ProtocolError("conflict", "Claude's window is set by Anthropic")
        try:
            user_models.check_name(model)
            # Atomic admission covers queued/unwinding/title jobs as well as the active turn.
            # The context manager deliberately releases the service lock during driver I/O.
            with self.service.model_change():
                current = self.driver.describe(model)
                validate("settings", current)
                if current["model"] != model:
                    raise ProtocolError("invalid_request", "Context settings belong to a different model")
                if current["revision"] != body["expected_revision"]:
                    raise Conflict("Context settings changed; reopen the panel")
                if body["operation"] == "measure":
                    result = self.driver.measure(model)
                else:
                    result = self.driver.apply(model, body["gb"], body["expected_revision"])
                validate("settings", result)
                if result["model"] != model:
                    raise ProtocolError("invalid_request", "Context settings belong to a different model")
                return result
        except (Uncertain, context_apply.ApplyFailed) as error:
            raise ProtocolError("model_unavailable", str(error)[:500]) from None
        except Conflict as error:
            raise ProtocolError("conflict", str(error)) from None
        except Unsupported as error:
            raise ProtocolError("model_unavailable", str(error)[:500]) from None
        except ProtocolError:
            raise
        except (OSError, ValueError) as error:
            raise ProtocolError("model_unavailable", "Couldn't change context memory; reopen the panel to check its state") from error

    def recovery(self):
        try:
            result = context_recovery.inspect(self.driver.lease("recovery").directory)
            if not result["reserved"] and getattr(getattr(self.driver, "runner", None), "uncertain", False):
                return {"reserved": True, "owner_running": True,
                        "recovery": "This service cannot verify its model state. Restart Nuru and Ollama before continuing."}
            validate("recovery", result)
            return result
        except (OSError, ValueError):
            return {"reserved": True, "owner_running": True,
                    "recovery": "Ownership could not be checked. Stop the owning service and inspect with nuru-serve compute check."}

    def recover(self, body):
        if (not isinstance(body, dict) or set(body) != {"run_id", "ollama_restarted"}
                or not isinstance(body["run_id"], str) or len(body["run_id"]) != 32
                or any(c not in "0123456789abcdef" for c in body["run_id"])
                or body["ollama_restarted"] is not True):
            raise ProtocolError("invalid_request", "Give the exact reservation ID and confirm restarting Ollama")
        try:
            with self.service.model_change():
                context_recovery.release(body["run_id"], ollama_restarted=True,
                                         directory=self.driver.lease("recovery").directory)
            return self.recovery()
        except OSError:
            raise ProtocolError("conflict", "Could not verify or clear ownership; inspect with nuru-serve compute check") from None
        except ValueError as error:
            raise ProtocolError("conflict", str(error)[:500]) from None
