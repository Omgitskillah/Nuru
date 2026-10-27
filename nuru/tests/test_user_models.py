"""core.user_models (TODO F22): a user's settings for a local model family. Names Nuru will pass on, one
bundle per family published whole, revisions never reused (a reset included), one lock across
processes, the snapshot a process runs with, the profile's limits, and the check build and save."""
import json
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

from core import main_models, model_profile, session, user_models
from core.modelfile import HeaderError, check_header, render_header
from core.providers import ollama
from core.user_models import INITIAL, Bundle, ConfigError, Conflict

MODELFILES = Path(__file__).resolve().parents[1] / "modelfiles"
SYSTEM_MD = MODELFILES / "system.md"
HEADER = "FROM __BASE_MODEL__\nPARAMETER temperature 0.7\n"
PROFILE = 'think: {"off": false, low: true, high: true}\ndefault: "off"\n'


@pytest.fixture
def models(tmp_path):
    return tmp_path / "models"


# ---------------------------------------------------------------------- names
@pytest.mark.parametrize("name, family", [
    ("llama3.1:8b", "llama3.1"), ("library/qwen3.5:latest", "qwen3.5"), ("openbmb/minicpm5:fp16", "minicpm5"),
    ("nemotron-3.5-bolt:e4b", "nemotron-3.5-bolt"), ("a/b/c:X1", "c"), ("gemma4", "gemma4"),
])
def test_a_model_name_gives_its_family(name, family):
    assert user_models.family_of(name) == family


@pytest.mark.parametrize("name", [
    "", "-rf", "--help", "../etc", "/abs/path", "a b", "Qwen3.5", "x\n", "x\x00", "x:", ":tag", "a/b/c/d:e",
    "a" * 257, None, 5, "x:-tag", "a//b", "ns/../x", "a" * 65 + ":tag",  # a family is at most 64
])
def test_a_name_nuru_wont_pass_on_is_refused(name):
    with pytest.raises(ConfigError):
        user_models.family_of(name)


# ---------------------------------------------------------------------- bundles and revisions
def test_nothing_saved_is_revision_initial_and_a_save_is_a_fresh_private_file(models):
    assert user_models.read("llama3.1", models) == Bundle("llama3.1", INITIAL)
    saved = user_models.publish("llama3.1", INITIAL, HEADER, PROFILE, models)
    assert saved.custom and len(saved.revision) == 32 and saved.revision != INITIAL
    assert user_models.read("llama3.1", models) == saved
    on_disk = json.loads((models / "llama3.1.json").read_text())
    assert on_disk == {"format": 1, "family": "llama3.1", "revision": saved.revision, "header": HEADER, "profile": PROFILE}
    if os.name != "nt":
        assert stat.S_IMODE((models / "llama3.1.json").stat().st_mode) == 0o600
        assert stat.S_IMODE(models.stat().st_mode) == 0o700
    assert [p.name for p in models.iterdir()] == ["llama3.1.json", "models.lock"] or \
        sorted(p.name for p in models.iterdir()) == ["llama3.1.json", "models.lock"], "no temporary file left"


def test_a_save_or_reset_from_a_stale_revision_is_refused_even_after_a_reset_and_recreate(models):
    """Astra's ABA case: read r1, someone resets and saves again; r1 must not pass."""
    first = user_models.publish("llama3.1", INITIAL, HEADER, PROFILE, models)
    reset = user_models.reset("llama3.1", first.revision, models)
    assert not reset.custom and reset.revision not in (INITIAL, first.revision), "a reset is a revision too"
    again = user_models.publish("llama3.1", reset.revision, HEADER, PROFILE, models)
    for stale in (first.revision, INITIAL):
        with pytest.raises(Conflict) as refused:
            user_models.publish("llama3.1", stale, "FROM __BASE_MODEL__\n", PROFILE, models)
        assert refused.value.current == again
        with pytest.raises(Conflict):
            user_models.reset("llama3.1", stale, models)
    assert user_models.read("llama3.1", models) == again


def test_a_save_interrupted_before_it_is_published_leaves_the_old_bundle_whole(models, monkeypatch):
    old = user_models.publish("llama3.1", INITIAL, HEADER, PROFILE, models)
    def interrupted(source, target):
        raise KeyboardInterrupt
    monkeypatch.setattr(user_models.os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        user_models.publish("llama3.1", old.revision, "FROM __BASE_MODEL__\n", "default: low\n", models)
    monkeypatch.undo()
    assert user_models.read("llama3.1", models) == old
    assert sorted(p.name for p in models.iterdir()) == ["llama3.1.json", "models.lock"], "its temporary file is gone"


def test_two_saves_from_the_same_revision_one_wins_the_other_is_told(models):
    base = user_models.publish("llama3.1", INITIAL, HEADER, PROFILE, models)
    start, results = threading.Barrier(2), []
    def save(header):
        start.wait()
        try:
            results.append(user_models.publish("llama3.1", base.revision, header, PROFILE, models))
        except Conflict as conflict:
            results.append(conflict)
    threads = [threading.Thread(target=save, args=(f"FROM __BASE_MODEL__\n# {i}\n",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    [won] = [r for r in results if isinstance(r, Bundle)]
    [lost] = [r for r in results if isinstance(r, Conflict)]
    assert lost.current == won == user_models.read("llama3.1", models)


@pytest.mark.parametrize("content, problem", [
    (b"{not json", "isn't readable"),
    (json.dumps({"format": 2, "family": "llama3.1", "revision": "0" * 32, "header": None, "profile": None}).encode(),
     "another version of Nuru"),
    (json.dumps({"format": True, "family": "llama3.1", "revision": "0" * 32, "header": None, "profile": None}).encode(),
     "isn't a model settings file"),
    (json.dumps({"format": 1, "family": "qwen3.5", "revision": "0" * 32, "header": None, "profile": None}).encode(),
     "isn't a model settings file"),
    (json.dumps({"format": 1, "family": "llama3.1", "revision": "initial", "header": None, "profile": None}).encode(),
     "isn't a model settings file"),
    (json.dumps({"format": 1, "family": "llama3.1", "revision": "0" * 32, "header": "x", "profile": None}).encode(),
     "isn't a model settings file"),
    (json.dumps({"format": 1, "family": "llama3.1", "revision": "0" * 32, "header": None, "profile": None,
                 "extra": 1}).encode(), "isn't a model settings file"),
    (b"[]", "isn't a model settings file"),
    (b" " * (user_models.BUNDLE_BYTES + 1), "too large"),
], ids=["not-json", "newer-format", "bool-format", "other-family", "bad-revision", "half-pair", "extra-key", "list", "huge"])
def test_a_bundle_nuru_cant_trust_is_refused_with_why(models, content, problem):
    models.mkdir()
    (models / "llama3.1.json").write_bytes(content)
    with pytest.raises(ConfigError, match=problem):
        user_models.read("llama3.1", models)


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need extra rights on Windows")
def test_a_symbolic_link_for_the_folder_or_a_bundle_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "linked").symlink_to(real)
    with pytest.raises(ConfigError, match="symbolic link"):
        user_models.publish("llama3.1", INITIAL, HEADER, PROFILE, tmp_path / "linked")
    models = tmp_path / "models"
    models.mkdir()
    (real / "elsewhere.json").write_text("{}")
    (models / "llama3.1.json").symlink_to(real / "elsewhere.json")
    with pytest.raises(ConfigError, match="symbolic link"):
        user_models.read("llama3.1", models)
    assert (real / "elsewhere.json").read_text() == "{}"


def test_a_family_name_can_never_reach_outside_the_folder(models):
    for family in ("../x", "..", ".", "a/b", "A", ""):
        with pytest.raises(ConfigError):
            user_models.read(family, models)


# ---------------------------------------------------------------------- what a process runs with
def test_a_process_runs_with_the_bundles_it_read_first_and_a_later_save_waits_for_a_restart(models, monkeypatch, capsys):
    saved = user_models.publish("llama3.1", INITIAL, HEADER, PROFILE, models)
    (models / "broken.json").write_text("{")
    user_models.publish("mistral", INITIAL, "FROM __BASE_MODEL__\nSYSTEM mine\n", PROFILE, models)  # by hand, say
    user_models.publish("phi4", INITIAL, HEADER, "default: sometimes\n", models)
    user_models.publish("gemma4", INITIAL, HEADER, PROFILE + "bad: 1\n1: 2\n", models)  # Astra's #62 probe
    (models / "Not A Family.json").write_text("{}")
    monkeypatch.setenv("NURU_MODELS_DIR", str(models))
    monkeypatch.setattr(user_models, "_active", None)
    assert set(user_models.active()) == {"llama3.1"}
    err = capsys.readouterr().err
    assert all(f"ignoring your settings for {f}" in err for f in ("broken", "mistral", "phi4", "gemma4")), err
    assert user_models.header("llama3.1:8b") == HEADER and user_models.custom("llama3.1:70b")
    assert user_models.profile_entry("llama3.1:8b") == {"think": {"off": False, "low": True, "high": True}, "default": "off"}

    user_models.publish("llama3.1", saved.revision, "FROM __BASE_MODEL__\n", PROFILE, models)
    user_models.publish("mistral-nemo", INITIAL, HEADER, PROFILE, models)
    assert user_models.header("llama3.1:8b") == HEADER, "the saved change applies when Nuru restarts"
    assert not user_models.custom("mistral-nemo:12b"), "a family added since is offered after a restart"
    assert user_models.header("qwen3.5:4b") is None and user_models.header("Not/A:valid name") is None


def test_no_folder_means_nuru_s_own_settings(tmp_path):
    assert user_models.snapshot(tmp_path / "none") == {}


def test_a_reset_family_runs_on_nuru_s_settings_but_its_revision_is_kept(models, monkeypatch):
    first = user_models.publish("qwen3.5", INITIAL, HEADER, PROFILE, models)
    reset = user_models.reset("qwen3.5", first.revision, models)
    monkeypatch.setattr(user_models, "_active", user_models.snapshot(models))
    assert user_models.active()["qwen3.5"] == reset and not user_models.custom("qwen3.5:4b")
    assert model_profile.for_model("qwen3.5:4b") == model_profile.for_model("qwen3.5:4b", model_profile.PROFILES.with_name("profiles.yaml"))


# ---------------------------------------------------------------------- profiles
def qwen_profile_text():
    entry = yaml.safe_load(model_profile.PROFILES.read_text())["qwen3.5"]
    return yaml.safe_dump({k: v for k, v in entry.items() if k != "supported"}, sort_keys=False)


def test_nuru_s_own_qwen_profile_round_trips_as_a_user_s(monkeypatch):
    entry = user_models.load_profile(qwen_profile_text())
    assert model_profile.users(entry, "qwen3.5") == model_profile.for_model("qwen3.5:4b"), "sampling nests 3 deep and is kept"


@pytest.mark.parametrize("text, problem", [
    ("base: &a {x: 1}\nthink: *a\n", "anchors or aliases"),
    ("a: {b: {c: {d: 1}}}\n", "deeper than 3"),
    ("\n".join(f"k{i}: {i}" for i in range(40)), "more than 32 entries"),
    ("- off\n- low\n", "must be a mapping"),
    ("think: [unclosed\n", "isn't valid YAML"),
    ("think: \x07\n", "control character"),
    (PROFILE + "bad: 1\n1: 2\n", "keys must be names, not 1"),
    ('think: {"off": false, low: true, high: true, null: false}\ndefault: "off"\n', "keys must be names, not None"),
    ("x: " + "a" * user_models.PROFILE_BYTES, "longer than 16 KiB"),
], ids=["alias", "too-deep", "too-many", "list", "bad-yaml", "control", "number-key", "null-key", "huge"])
def test_a_profile_beyond_the_limits_is_refused(text, problem):
    with pytest.raises(ConfigError if "KiB" not in problem and "control" not in problem else ValueError, match=problem):
        user_models.load_profile(text)


@pytest.mark.parametrize("text, problem", [
    ("supported: true\n" + PROFILE, "think, default and sampling only"),
    ('think: {"off": false, low: true, high: true}\ndefault: "off"\nsampling: {thinking: {top_k: true}}\n',
     "finite numbers|top_k"),
    ('think: {"off": false, low: true, high: true}\ndefault: "off"\nsampling: {thinking: {temperature: .nan}}\n',
     "finite numbers"),
    ('think: {"off": false, low: true}\ndefault: "off"\n', "think needs exactly"),
], ids=["supported", "bool-top-k", "nan", "missing-level"])
def test_a_user_profile_is_checked_as_nuru_s_are(text, problem):
    with pytest.raises(ValueError, match=problem):
        model_profile.users(user_models.load_profile(text), "llama3.1")


# ---------------------------------------------------------------------- the check build
def test_a_check_build_uses_a_fresh_name_of_its_own_and_always_removes_it(monkeypatch):
    built, removed = [], []
    tokens = iter(["0" * 16, "1" * 16])
    monkeypatch.setattr(user_models.secrets, "token_hex", lambda n: next(tokens))
    problem = user_models.check_build("FROM x\n", build=lambda m, r: built.append((m, r)), remove=removed.append,
                                      listed=lambda: ["nuru-check-" + "0" * 16 + ":latest", "nuru:latest"])
    assert problem is None
    assert built == [("nuru-check-" + "1" * 16, "FROM x\n")], "a name Ollama already has is never built over"
    assert removed == ["nuru-check-" + "1" * 16]


def test_a_failed_slow_or_unremovable_check_build_says_what_ollama_said(capsys):
    def fails(model, rendered):
        raise subprocess.CalledProcessError(1, ["ollama"], stderr="Error: " + "x" * 1000)
    def slow(model, rendered):
        raise subprocess.TimeoutExpired(["ollama"], 300)
    def stuck(model):
        raise subprocess.TimeoutExpired(["ollama", "rm"], 60)
    removed = []
    problem = user_models.check_build("FROM x\n", build=fails, remove=removed.append, listed=list)
    assert problem == "Ollama couldn't build it: " + ("Error: " + "x" * 1000)[-user_models.ERROR_CHARS:] and removed
    assert user_models.check_build("FROM x\n", build=slow, remove=removed.append, listed=list) == \
        "Ollama took longer than 300 s to build it"
    assert user_models.check_build("FROM x\n", build=fails, remove=stuck, listed=list).startswith("Ollama couldn't build it: x"), \
        "the build's failure, not the cleanup's"
    left = user_models.check_build("FROM x\n", build=lambda m, r: None, remove=stuck, listed=list)
    assert left.startswith("Nuru couldn't remove the check model nuru-check-") and "ollama rm nuru-check-" in left, \
        "built, but a model of Nuru's is left: that is the problem then"
    assert "couldn't remove the check model nuru-check-" in capsys.readouterr().err


@pytest.fixture
def fake_ollama(tmp_path, monkeypatch):
    """A stand-in `ollama` program first on PATH: `create` runs FAKE_CREATE (a shell snippet), `rm` logs."""
    if os.name == "nt":
        pytest.skip("subprocess finds only .exe programs on Windows")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "ollama.log"
    script = bin_dir / "ollama"
    script.write_text(f'#!/bin/sh\necho "$@" >> {log}\nif [ "$1" = create ]; then eval "$FAKE_CREATE"; fi\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log


def test_a_stuck_ollama_create_is_killed_at_its_time_limit_and_the_check_name_removed(fake_ollama, monkeypatch):
    monkeypatch.setenv("FAKE_CREATE", "echo $$ > " + str(fake_ollama) + ".pid; exec sleep 30")
    problem = user_models.check_build("FROM x\n", build=lambda m, r: ollama.build_model(m, r, timeout=0.5),
                                      remove=ollama.remove_model, listed=list)
    assert problem == "Ollama took longer than 0.5 s to build it"
    pid = int(Path(str(fake_ollama) + ".pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # killed and reaped, not left running
    create, remove = fake_ollama.read_text().splitlines()
    assert create.startswith("create nuru-check-") and remove == "rm " + create.split()[1]


def test_build_model_returns_the_rendering_it_built_and_removes_its_file(fake_ollama, monkeypatch):
    monkeypatch.setenv("FAKE_CREATE", 'cat "$4" >> ' + str(fake_ollama))
    assert ollama.build_model("nuru-x", "FROM base\nPARAMETER top_k 5\n") == "FROM base\nPARAMETER top_k 5\n"
    command, *built = fake_ollama.read_text().splitlines()
    assert command.startswith("create nuru-x -f ") and built == ["FROM base", "PARAMETER top_k 5"]
    assert not Path(command.split()[-1]).exists()
    ollama.remove_model("nuru-x")
    assert fake_ollama.read_text().splitlines()[-1] == "rm nuru-x"


def test_build_model_passes_the_rendering_to_ollama_create_and_removes_its_file(fake_ollama, monkeypatch):
    monkeypatch.setenv("FAKE_CREATE", 'cat "$4" >> ' + str(fake_ollama) + "; echo 'Error: bad' >&2; exit 1")
    with pytest.raises(subprocess.CalledProcessError) as failed:
        ollama.build_model("nuru-x", "FROM base\n")
    assert "Error: bad" in failed.value.stderr
    lines = fake_ollama.read_text().splitlines()
    assert lines[0].startswith("create nuru-x -f ") and lines[1] == "FROM base"
    assert not Path(lines[0].split()[-1]).exists()


# ---------------------------------------------------------------------- saving
def checked(problem=None, during=None):
    seen = []
    def check(rendered):
        seen.append(rendered)
        if during:
            during()
        return problem
    check.seen = seen
    return check


def test_a_save_is_checked_built_once_then_published(models):
    check = checked()
    saved = user_models.save("llama3.1", INITIAL, HEADER, PROFILE, base_model="llama3.1:8b",
                             capabilities={"completion", "tools"}, check=check, directory=models)
    assert user_models.read("llama3.1", models) == saved
    [rendered] = check.seen
    assert rendered == render_header(HEADER, "llama3.1:8b", SYSTEM_MD)


@pytest.mark.parametrize("header, profile, base, problem", [
    ("FROM __BASE_MODEL__\nSYSTEM hi\n", PROFILE, "llama3.1:8b", "SYSTEM isn't allowed"),
    ("FROM __BASE_MODEL__\nCAPABILITY vision\n", PROFILE, "llama3.1:8b", "capability 'vision'"),
    (HEADER, "default: sometimes\n", "llama3.1:8b", "think needs exactly"),
    (HEADER, PROFILE, "qwen3.5:4b", "isn't a llama3.1 model"),
], ids=["system", "capability-the-base-lacks", "bad-profile", "other-family"])
def test_a_save_that_fails_its_checks_writes_nothing_and_builds_nothing(models, header, profile, base, problem):
    before = user_models.publish("llama3.1", INITIAL, HEADER, PROFILE, models)
    check = checked()
    with pytest.raises(ValueError, match=problem):
        user_models.save("llama3.1", before.revision, header, profile, base_model=base,
                         capabilities={"completion", "tools"}, check=check, directory=models)
    assert check.seen == [] and user_models.read("llama3.1", models) == before


def test_a_save_ollama_cant_build_writes_nothing(models):
    with pytest.raises(ConfigError, match="Ollama couldn't build it: Error: unknown template"):
        user_models.save("llama3.1", INITIAL, HEADER, PROFILE, base_model="llama3.1:8b", capabilities={"tools"},
                         check=checked("Ollama couldn't build it: Error: unknown template"), directory=models)
    assert user_models.read("llama3.1", models) == Bundle("llama3.1", INITIAL)


def test_a_save_whose_check_model_cant_be_removed_is_not_published(models):
    before = user_models.publish("llama3.1", INITIAL, HEADER, PROFILE, models)
    def stuck(model):
        raise subprocess.TimeoutExpired(["ollama", "rm"], 60)
    check = lambda rendered: user_models.check_build(rendered, build=lambda m, r: None, remove=stuck, listed=list)  # noqa: E731
    with pytest.raises(ConfigError, match="couldn't remove the check model nuru-check-"):
        user_models.save("llama3.1", before.revision, "FROM __BASE_MODEL__\n", PROFILE, base_model="llama3.1:8b",
                         capabilities={"tools"}, check=check, directory=models)
    assert user_models.read("llama3.1", models) == before


def test_a_save_raced_by_another_while_it_builds_is_refused_and_overwrites_nothing(models):
    other = []
    def meanwhile():
        other.append(user_models.publish("llama3.1", INITIAL, "FROM __BASE_MODEL__\n# theirs\n", PROFILE, models))
    with pytest.raises(Conflict):
        user_models.save("llama3.1", INITIAL, HEADER, PROFILE, base_model="llama3.1:8b", capabilities={"tools"},
                         check=checked(during=meanwhile), directory=models)
    assert user_models.read("llama3.1", models) == other[0]
    with pytest.raises(Conflict):  # and one already stale doesn't even build
        user_models.save("llama3.1", INITIAL, HEADER, PROFILE, base_model="llama3.1:8b", capabilities={"tools"},
                         check=checked(during=lambda: pytest.fail("built")), directory=models)


# ---------------------------------------------------------------------- adding a family
@pytest.mark.parametrize("shown, problem", [
    ({"capabilities": ["embedding"]}, "can't write text"),
    ({"capabilities": ["completion", "vision"]}, "can't use tools"),
    ({"capabilities": ["completion", "tools"], "messages": [{"role": "user", "content": "hi"}]}, "its own conversation history"),
    ({"capabilities": ["completion", "tools", "thinking"]}, None),
])
def test_a_pulled_model_runs_nuru_only_with_text_and_tools_and_no_history_of_its_own(shown, problem):
    found = user_models.base_problem(shown)
    assert (found is None) if problem is None else problem in found


@pytest.mark.parametrize("capabilities, levels", [
    (["completion", "tools", "thinking"], ["off", "low"]),
    (["completion", "tools"], []),
])
def test_a_new_family_starts_from_the_model_s_own_parameters_and_thinks_only_if_it_can(capabilities, levels):
    shown = {"capabilities": capabilities, "parameters": 'stop "<end_of_turn>"\ntemperature 1\n"""weird"""'}
    header, profile = user_models.starting(shown)
    assert check_header(header, set(capabilities)) == [("PARAMETER", "num_predict 8192")], "its own stay comments"
    assert '#   stop "<end_of_turn>"' in header
    choice = main_models.thinking(model_profile.users(user_models.load_profile(profile), "x"), "thinking" in capabilities)
    assert [level["id"] for level in choice[0]] == levels


# ---------------------------------------------------------------------- where Nuru uses them
def test_a_user_family_builds_from_its_header_is_listed_untested_and_runs_on_its_profile(models, monkeypatch):
    user_models.publish("llama3.1", INITIAL, HEADER, PROFILE, models)
    monkeypatch.setattr(user_models, "_active", user_models.snapshot(models))
    built, created = [], []
    monkeypatch.setattr(session, "base_info", lambda name: {"capabilities": ["completion", "tools"]})
    monkeypatch.setattr(session, "build_model", lambda model, rendered: built.append((model, rendered)) or rendered)
    monkeypatch.setattr(session, "create_model", lambda *args: created.append(args) or "SYSTEM \"\"\"x\"\"\"")
    rendered = session._build("nuru-llama3.1-8b", "llama3.1:8b")
    assert built == [("nuru-llama3.1-8b", render_header(HEADER, "llama3.1:8b", SYSTEM_MD))] and rendered == built[0][1]
    session._build("nuru", "qwen3.5:4b")
    assert created == [("nuru", "qwen3.5:4b", session._modelfile_for("qwen3.5:4b"))], "Nuru's own, as before"

    assert model_profile.for_model("llama3.1:8b").think_map == {"off": False, "low": True, "high": True}
    tags = [{"name": n} for n in ("llama3.1:8b", "qwen3.5:4b", "other/llama3.1:embed", "llama3.1:chat")]
    monkeypatch.setattr(main_models.requests, "get", lambda url, timeout: type("R", (), {"json": lambda self: {"models": tags}})())
    capabilities = {"other/llama3.1:embed": ["embedding"], "llama3.1:chat": ["completion"]}  # Astra's #62 probe
    monkeypatch.setattr(main_models, "model_info", lambda name: {"capabilities": capabilities.get(name, ["completion", "tools", "thinking"])})
    monkeypatch.setattr(main_models, "_cache", None)
    monkeypatch.setattr(main_models, "api_key", lambda: None)
    labels = {c["id"]: c["label"] for c in main_models.choices("qwen3.5:4b")["choices"]}
    assert labels == {"qwen3.5:4b": "qwen3.5:4b", "llama3.1:8b": "llama3.1:8b (untested)"}


@pytest.mark.parametrize("shown, header, problem", [
    ({"capabilities": ["embedding"]}, HEADER, "other/llama3.1:embed can't run Nuru: it can't write text"),
    ({"capabilities": ["completion", "tools"], "messages": [{"role": "user"}]}, HEADER, "its own conversation history"),
    ({"capabilities": ["completion", "tools"]}, "FROM __BASE_MODEL__\nCAPABILITY thinking\n", "capability 'thinking'"),
], ids=["embedding", "history", "capability"])
def test_a_user_header_is_built_only_on_a_base_that_can_run_it(models, monkeypatch, shown, header, problem):
    """Astra's #62 review: what was checked when it was saved says nothing of another model of the family."""
    user_models.publish("llama3.1", INITIAL, header, PROFILE, models)
    monkeypatch.setattr(user_models, "_active", user_models.snapshot(models))
    monkeypatch.setattr(session, "base_info", lambda name: shown)
    monkeypatch.setattr(session, "build_model", lambda *args: pytest.fail("built"))
    with pytest.raises(ValueError, match=problem):
        session._build("nuru-x", "other/llama3.1:embed")


def test_a_header_saved_by_a_user_gets_nuru_s_prompt_whatever_the_base_had():
    rendered = render_header(HEADER, "llama3.1:8b", SYSTEM_MD)
    assert rendered.count('SYSTEM """') == 1 and rendered.rstrip().endswith('"""')
    assert rendered.splitlines()[0] == "FROM llama3.1:8b"
    with pytest.raises(HeaderError):
        render_header(HEADER + 'SYSTEM """mine"""\n', "llama3.1:8b", SYSTEM_MD)


def test_the_tests_never_see_a_developer_s_own_settings():
    assert Path(os.environ["NURU_MODELS_DIR"]).name.startswith("nuru-test-models-")
    assert Path(os.environ["NURU_MODELS_DIR"]) != Path.home() / ".nuru" / "models"
    assert sys.modules["core.session"] is session
