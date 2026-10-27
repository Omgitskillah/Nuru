"""A user's own settings for a local model family (TODO F22): the Modelfile header and the profile that
go with the family's models, in place of Nuru's (modelfiles/), or for a family Nuru has none for.

They live in ~/.nuru/models (NURU_MODELS_DIR), one bundle per family, <family>.json:
{"format": 1, "family", "revision", "header", "profile"}. A single file, so the header and the profile
are always saved together; os.replace publishes it whole. `revision` is a fresh random token on every
save, never reused: a reset is saved too, as a bundle with no header and profile (Nuru's own then
apply), so a settings window that read an older revision can't overwrite what came after. Saving,
resetting and reading them all at start take one lock, across processes (models.lock).

A process reads them once, when it first needs them (active()), and keeps that until it ends: a saved
change applies when Nuru restarts, never under a running turn. A save is checked first: the header
strictly (core.modelfile), the profile as Nuru's are (core.model_profile), and the model built once by
Ollama under a throwaway name (check_build); only then is it published, if its revision is still the
one read.
"""

import json
import os
import re
import secrets
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.vault_identity import _exclusive

FORMAT = 1
INITIAL = "initial"  # the revision of a family nothing was ever saved for
BUNDLE_BYTES = 160 * 1024
PROFILE_BYTES = 16 * 1024
_NAME = re.compile(r"(?:[a-z0-9][a-z0-9._-]{0,63}/){0,2}[a-z0-9][a-z0-9._-]{0,127}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,127})?")
_FAMILY = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_REVISION = re.compile(r"[0-9a-f]{32}")
_PROFILE_DEPTH, _PROFILE_ITEMS = 3, 32  # profiles.yaml's own shape: sampling -> thinking -> options


class ConfigError(ValueError):
    """A name, a bundle or a save that can't be used, and why."""


class Conflict(Exception):
    """Another save or reset came first: `current` is what is saved now."""

    def __init__(self, current: "Bundle") -> None:
        super().__init__(f"{current.family}'s settings changed since they were read")
        self.current = current


@dataclass(frozen=True)
class Bundle:
    family: str
    revision: str
    header: str | None = None  # None: Nuru's own settings apply (never saved, or reset)
    profile: str | None = None

    @property
    def custom(self) -> bool:
        return self.header is not None


def models_dir() -> Path:
    return Path(os.environ.get("NURU_MODELS_DIR") or Path.home() / ".nuru" / "models")


def check_name(name) -> str:
    """An Ollama model name Nuru will pass on (to a URL, a path, `ollama create`): lowercase
    namespace/name, an optional :tag, nothing else."""
    if not isinstance(name, str) or len(name) > 256 or not _NAME.fullmatch(name):
        raise ConfigError(f"not a model name Nuru can use: {str(name)[:80]!r}")
    return name


def family_of(name: str) -> str:
    """The family a model's settings go with: its name without namespace and tag (model_profile.family)."""
    family = check_name(name).split("/")[-1].split(":")[0]
    if not _FAMILY.fullmatch(family):
        raise ConfigError(f"not a model family: {family[:80]!r}")
    return family


# ---------------------------------------------------------------------- the bundles
def _path(directory: Path, family: str) -> Path:
    if not _FAMILY.fullmatch(family):
        raise ConfigError(f"not a model family: {family[:80]!r}")
    if directory.is_symlink() or (directory / f"{family}.json").is_symlink():
        raise ConfigError("Nuru's model settings may not be a symbolic link")
    return directory / f"{family}.json"


def read(family: str, directory: Path | None = None) -> Bundle:
    """What is saved for `family`; Bundle(family, INITIAL) if nothing ever was."""
    path = _path(directory or models_dir(), family)
    try:
        with open(path, "rb") as file:
            data = file.read(BUNDLE_BYTES + 1)
    except FileNotFoundError:
        return Bundle(family, INITIAL)
    if len(data) > BUNDLE_BYTES:
        raise ConfigError(f"{path.name} is too large")
    try:
        saved = json.loads(data.decode("utf-8"))
    except ValueError as error:
        raise ConfigError(f"{path.name} isn't readable: {error}") from None
    if not isinstance(saved, dict) or type(saved.get("format")) is not int:
        raise ConfigError(f"{path.name} isn't a model settings file")
    if saved["format"] != FORMAT:
        raise ConfigError(f"{path.name} was saved by another version of Nuru (format {saved['format']})")
    if set(saved) != {"format", "family", "revision", "header", "profile"} or saved["family"] != family \
            or not isinstance(saved["revision"], str) or not _REVISION.fullmatch(saved["revision"]) \
            or not (all(isinstance(saved[k], str) for k in ("header", "profile"))
                    or (saved["header"] is None and saved["profile"] is None)):
        raise ConfigError(f"{path.name} isn't a model settings file")
    return Bundle(family, saved["revision"], saved["header"], saved["profile"])


def _lock(directory: Path):
    if directory.is_symlink():
        raise ConfigError("Nuru's model settings may not be a symbolic link")
    return _exclusive(directory / "models.lock", busy=ConfigError("another Nuru is saving model settings; try again"))


def publish(family: str, expected: str, header: str | None, profile: str | None,
            directory: Path | None = None) -> Bundle:
    """Save `header` and `profile` for `family` (both None: reset to Nuru's), if what is saved is still
    revision `expected`; else Conflict, and nothing is written. Checks nothing else: see save()."""
    directory = directory or models_dir()
    with _lock(directory):
        current = read(family, directory)
        if current.revision != expected:
            raise Conflict(current)
        saved = Bundle(family, secrets.token_hex(16), header, profile)
        _write(_path(directory, family), json.dumps({"format": FORMAT, "family": family, "revision": saved.revision,
                                                     "header": header, "profile": profile}, ensure_ascii=False))
        return saved


def _write(path: Path, text: str) -> None:
    """Replace `path` whole: a reader sees the old file or the new one, never part of either."""
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def snapshot(directory: Path | None = None) -> dict[str, Bundle]:
    """Every family's saved bundle, read together under the lock. One that can't be read, or whose
    header or profile no longer passes the checks (edited by hand, say), is left out (Nuru's own
    settings apply) and said so on stderr: Nuru still starts."""
    from core import model_profile
    from core.modelfile import check_header

    directory = directory or models_dir()
    if not directory.is_dir():
        return {}
    found = {}
    with _lock(directory):
        for path in sorted(directory.glob("*.json")):
            if not _FAMILY.fullmatch(path.stem):
                continue
            try:
                bundle = read(path.stem, directory)
                if bundle.custom:
                    check_header(bundle.header)
                    model_profile.users(load_profile(bundle.profile), bundle.family)
                found[path.stem] = bundle
            except ValueError as error:  # ConfigError, HeaderError, a profile's
                print(f"nuru: ignoring your settings for {path.stem}: {error}", file=sys.stderr)
    return found


_active: dict[str, Bundle] | None = None
_active_lock = threading.Lock()


def active() -> dict[str, Bundle]:
    """The bundles this process runs with: read once (snapshot), kept until it ends."""
    global _active
    with _active_lock:
        if _active is None:
            _active = snapshot()
        return _active


def _custom(model: str) -> Bundle | None:
    try:
        bundle = active().get(family_of(model))
    except ConfigError:  # a name Nuru's own files may still know, as before
        return None
    return bundle if bundle is not None and bundle.custom else None


def custom(model: str) -> bool:
    """Whether `model` runs on the user's settings, not Nuru's: an untested family, or edited ones."""
    return _custom(model) is not None


def header(model: str) -> str | None:
    """The user's header for `model`'s family this process runs with, or None for Nuru's."""
    bundle = _custom(model)
    return bundle.header if bundle else None


def profile_entry(model: str) -> dict | None:
    """The user's profile for `model`'s family this process runs with, parsed, or None for Nuru's."""
    bundle = _custom(model)
    return load_profile(bundle.profile) if bundle else None


def load_profile(text: str) -> dict:
    """A profile as a user wrote it: YAML of profiles.yaml's shape (think, default, sampling), with no
    anchors or aliases, at most `_PROFILE_DEPTH` levels and `_PROFILE_ITEMS` entries in each."""
    from core.modelfile import check_text

    check_text(text, PROFILE_BYTES, "The profile")
    depth, counts = 0, []
    try:
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                raise ConfigError("the profile can't use YAML anchors or aliases")
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
                if counts:
                    counts[-1] += 1
                counts.append(0)
                if depth > _PROFILE_DEPTH:
                    raise ConfigError(f"the profile nests deeper than {_PROFILE_DEPTH} levels")
            elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
                counts.pop()
            elif isinstance(event, yaml.ScalarEvent) and counts:
                counts[-1] += 1
                if counts[-1] > 2 * _PROFILE_ITEMS:  # keys and values
                    raise ConfigError(f"the profile has more than {_PROFILE_ITEMS} entries in one place")
        entry = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ConfigError(f"the profile isn't valid YAML: {str(error)[:200]}") from None
    if not isinstance(entry, dict):
        raise ConfigError("the profile must be a mapping: think, default and sampling")
    return entry


# ---------------------------------------------------------------------- checking with Ollama
CHECK_PREFIX = "nuru-check-"
ERROR_CHARS = 500


def check_build(rendered: str, *, build, remove, listed) -> str | None:
    """Build `rendered` once under a fresh name of Nuru's own, then remove it: None if Ollama built it,
    else what Ollama said (at most ERROR_CHARS). `build(model, rendered)` and `remove(model)` are
    core.providers.ollama's build_model and remove_model, each with its own time limit; `listed()`
    gives the names Ollama has, so a name already there is never built over."""
    names = set(listed())
    alias = next(a for a in (f"{CHECK_PREFIX}{secrets.token_hex(8)}" for _ in range(8)) if f"{a}:latest" not in names
                 and a not in names)
    problem = None
    try:
        build(alias, rendered)
    except subprocess.TimeoutExpired as error:
        problem = f"Ollama took longer than {error.timeout:g} s to build it"
    except subprocess.CalledProcessError as error:
        problem = ((error.stderr or error.stdout or "").strip() or f"ollama create failed ({error.returncode})")[-ERROR_CHARS:]
    finally:
        try:
            remove(alias)
        except (OSError, subprocess.SubprocessError) as error:  # what the build said matters more
            print(f"nuru: couldn't remove the check model {alias}: {error}", file=sys.stderr)
    return problem


# ---------------------------------------------------------------------- saving, resetting, adding
def save(family: str, expected: str, header: str, profile: str, *, base_model: str, capabilities: set[str],
         check, directory: Path | None = None) -> Bundle:
    """Check, build once, then publish `header` and `profile` for `family`, if still at revision
    `expected` (Conflict otherwise, before and after building, as the build takes a while and runs
    outside the lock). `base_model` is a pulled model of the family, `capabilities` its own (from
    Ollama's /api/show, never the derived model's); `check(rendered)` is check_build bound to Ollama.
    ValueError (HeaderError, ConfigError) says what's wrong; nothing is saved then."""
    from core import model_profile
    from core.modelfile import check_header, render_header

    if family_of(base_model) != family:
        raise ConfigError(f"{base_model} isn't a {family} model")
    current = read(family, directory)
    if current.revision != expected:
        raise Conflict(current)
    check_header(header, capabilities)
    model_profile.users(load_profile(profile), family)
    problem = check(render_header(header, base_model, model_profile.PROFILES.parent / "system.md"))
    if problem:
        raise ConfigError(f"Ollama couldn't build it: {problem}")
    return publish(family, expected, header, profile, directory)


def reset(family: str, expected: str, directory: Path | None = None) -> Bundle:
    """Back to Nuru's own settings for `family` (none, for a family Nuru has none for), if still at
    revision `expected`; the reset is itself a revision."""
    return publish(family, expected, None, None, directory)


def base_problem(shown: dict) -> str | None:
    """Why a pulled model can't run Nuru, from its own /api/show; None if it can."""
    capabilities = set(shown.get("capabilities") or [])
    if "completion" not in capabilities:
        return "it can't write text"
    if "tools" not in capabilities:
        return "it can't use tools, and Nuru reads and writes your notes only through tools"
    if shown.get("messages"):
        return "it carries its own conversation history, which Nuru can't take out"
    return None


def starting(shown: dict) -> tuple[str, str]:
    """A header and a profile to start a family Nuru has no settings for, from a model's /api/show: its
    own parameters (which apply unless set here) as comments, Nuru's guard against runaway replies,
    and thinking levels only if it can think."""
    from core.modelfile import BASE_MODEL_PLACEHOLDER, GUARD_NUM_PREDICT

    own = [line.strip()[:200] for line in (shown.get("parameters") or "").splitlines() if line.strip()][:40]
    header = (f"FROM {BASE_MODEL_PLACEHOLDER}\n"
              + ("# The model's own parameters, which apply unless set below:\n" + "".join(f"#   {line}\n" for line in own)
                 if own else "")
              + f"PARAMETER num_predict {GUARD_NUM_PREDICT}\n")
    on = "true" if "thinking" in set(shown.get("capabilities") or []) else "false"
    profile = f'think: {{"off": false, low: {on}, high: {on}}}\ndefault: "off"\n'
    return header, profile
