"""Local administration of a model family's settings (TODO F22), behind /v1/models/settings and
/v1/models: what the plugin's settings show, save, reset and add. The contract is
model-settings-v1.schema.json; server.py refuses all of it on the remote listener, so only a client
signed in on this computer may change what model runs. The checks themselves are core.user_models'.

What is saved applies when Nuru restarts (user_models.active()): `revision` is what is saved,
`active_revision` what this service runs with.
"""

import json
from functools import cache
from pathlib import Path

import jsonschema
import requests
import yaml

from core import model_profile, user_models
from core.modelfile import default_header
from core.providers import ollama
from core.service.protocol import ProtocolError, _problem

SCHEMA_PATH = Path(__file__).with_name("model-settings-v1.schema.json")
MODELFILES = model_profile.PROFILES.parent
MAX_BODY_BYTES = 128 * 1024


@cache
def _validator(kind: str) -> jsonschema.Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator({**schema, "$ref": f"#/$defs/{kind}"})


def validate(kind: str, value) -> None:
    error = jsonschema.exceptions.best_match(_validator(kind).iter_errors(value))
    if error is not None:
        raise ProtocolError("invalid_request", _problem(error)[:500])


def nuru_settings(family: str) -> dict | None:
    """Nuru's own header and profile for `family`, as a user starts editing them; None if it has none."""
    entry = (yaml.safe_load(model_profile.PROFILES.read_text(encoding="utf-8")) or {}).get(family)
    path = MODELFILES / f"{family}.Modelfile"
    if not isinstance(entry, dict) or entry.get("supported") is not True or not path.exists():
        return None
    profile = yaml.safe_dump({k: v for k, v in entry.items() if k != "supported"}, sort_keys=False, allow_unicode=True)
    return {"header": default_header(path), "profile": profile}


class ModelSettings:
    """One request's worth: `default_model` is what new conversations start on; `show`, `names`,
    `build` and `remove` reach Ollama (core.providers.ollama's, by default)."""

    def __init__(self, default_model: str | None, *, show=None, names=None, build=None, remove=None,
                 directory: Path | None = None) -> None:
        self.default_model, self.directory = default_model, directory
        self.show = show or (lambda model: ollama.model_info(model))  # looked up when called, not now
        self.names = names or (lambda: [m["name"] for m in requests.get(f"{ollama.BASE}/api/tags", timeout=10)
                                        .json().get("models", [])])
        self.build = build or (lambda model, rendered: ollama.build_model(model, rendered))
        self.remove = remove or (lambda model: ollama.remove_model(model))

    def describe(self, model: str) -> dict:
        family = self._family(model)
        saved = user_models.read(family, self.directory)
        active = user_models.active().get(family)
        nuru = nuru_settings(family)
        effective = {"header": saved.header, "profile": saved.profile} if saved.custom else (nuru or {"header": None, "profile": None})
        body = {"model": model, "family": family, "custom": saved.custom, "revision": saved.revision,
                "active_revision": active.revision if active else user_models.INITIAL, **effective, "nuru": nuru,
                "default": self._is_default(family)}
        validate("settings", body)
        return body

    def save(self, body: dict) -> dict:
        validate("save_settings", body)
        model = body["model"]
        family = self._family(model)
        shown = self._shown(model)
        problem = user_models.base_problem(shown)
        if problem:
            raise ProtocolError("invalid_request", f"{model} can't run Nuru: {problem}")
        self._published(lambda: user_models.save(
            family, body["expected_revision"], body["header"], body["profile"], base_model=model,
            capabilities=set(shown.get("capabilities") or []), check=self._check, directory=self.directory))
        return self.describe(model)

    def reset(self, model: str, expected: str) -> dict:
        validate("revision", expected)
        family = self._family(model)
        if self._is_default(family) and nuru_settings(family) is None:
            raise ProtocolError("conflict", "New conversations start on this model, and Nuru has no settings of its "
                                            "own for it: choose another default model first.")
        self._published(lambda: user_models.reset(family, expected, self.directory))
        return self.describe(model)

    def add(self, body: dict) -> dict:
        """Set up a pulled model's family, if Nuru doesn't run it yet: from the model's own parameters,
        checked and built once like any save. `added` says whether it did."""
        validate("add_model", body)
        model = body["model"]
        family = self._family(model)
        shown = self._shown(model)
        problem = user_models.base_problem(shown)
        if problem:
            raise ProtocolError("invalid_request", f"{model} can't run Nuru: {problem}")
        saved = user_models.read(family, self.directory)
        if saved.custom or nuru_settings(family) is not None:
            return {**self.describe(model), "added": False}
        header, profile = user_models.starting(shown)
        self._published(lambda: user_models.save(
            family, saved.revision, header, profile, base_model=model,
            capabilities=set(shown.get("capabilities") or []), check=self._check, directory=self.directory))
        return {**self.describe(model), "added": True}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _family(model) -> str:
        try:
            return user_models.family_of(model)
        except user_models.ConfigError as error:
            raise ProtocolError("invalid_request", str(error)) from None

    def _is_default(self, family: str) -> bool:
        try:
            return bool(self.default_model) and user_models.family_of(self.default_model) == family
        except user_models.ConfigError:
            return False

    def _shown(self, model: str) -> dict:
        """The pulled model's own /api/show."""
        try:
            return self.show(model)
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 404:
                raise ProtocolError("not_found", f"Ollama has no model {model}: download it first") from None
            raise ProtocolError("model_unavailable", "Ollama couldn't say what the model can do") from None
        except (requests.RequestException, ValueError):
            raise ProtocolError("model_unavailable", "Ollama isn't answering") from None

    def _check(self, rendered: str) -> str | None:
        try:
            names = self.names()
        except (requests.RequestException, ValueError):
            return "Ollama isn't answering"
        return user_models.check_build(rendered, build=self.build, remove=self.remove, listed=lambda: names)

    @staticmethod
    def _published(publish) -> None:
        try:
            publish()
        except user_models.Conflict:
            # protocol v1's error body has no room for the revision: the client reads the settings again
            raise ProtocolError("conflict", "These settings changed since they were read: reload them.") from None
        except ValueError as error:  # HeaderError, ConfigError, a profile's
            raise ProtocolError("invalid_request", str(error)[:500]) from None
