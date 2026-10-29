import pytest
import yaml

from core import model_profile
from core.model_profile import family, for_model, for_runtime_model


def test_family_strips_the_tag_and_any_registry_namespace():
    assert family("qwen3.5:4b") == family("library/qwen3.5:latest") == family("qwen3.5") == "qwen3.5"


def test_granite_keeps_its_levels_and_runs_at_low():
    granite = for_model("granite4.2:3b")
    assert [granite.think(level) for level in ("off", "low", "high")] == [False, "low", "high"]
    assert granite.think(granite.default) == "low"
    assert granite.options("low") == {}  # its sampling stays in the Modelfile


def test_qwen_has_no_levels_so_low_and_high_mean_on_and_it_runs_with_thinking_off():
    qwen = for_model("qwen3.5:4b")
    assert [qwen.think(level) for level in ("off", "low", "high")] == [False, True, True]
    assert qwen.think(qwen.default) is False


def test_qwen_sampling_depends_on_whether_it_thinks():
    qwen = for_model("qwen3.5:4b")
    thinking, not_thinking = qwen.options(True)["options"], qwen.options(False)["options"]
    assert thinking["presence_penalty"] == not_thinking["presence_penalty"] == 0
    assert thinking != not_thinking


@pytest.mark.parametrize("name", ["qwen3", "gemma4", "llama3.1", "mistral-nemo", "lfm2.5-thinking"])
def test_legacy_families_are_explicitly_unsupported(name):
    with pytest.raises(ValueError, match=f"Unsupported model family '{name}'.*Legacy prompt"):
        for_model(name)


def test_an_unknown_family_never_silently_inherits_a_default_profile():
    with pytest.raises(ValueError, match="no explicit profile.*new-model"):
        for_model("new-model:3b")


@pytest.mark.parametrize("base", ["qwen3.5:4b", "granite4.2:3b"])
@pytest.mark.parametrize("alias", ["nuru", "nuru:latest", "my-nuru:review"])
def test_a_derived_runtime_alias_inherits_the_base_profile(base, alias):
    assert for_runtime_model(alias, derived_model=alias, base_model=base) == for_model(base)


def test_a_bare_derived_alias_and_latest_tag_are_equivalent():
    assert for_runtime_model("nuru:latest", derived_model="nuru", base_model="qwen3.5:4b") == for_model("qwen3.5")


def test_an_independent_planner_uses_its_own_profile():
    assert for_runtime_model("granite4.2:3b", derived_model="nuru", base_model="qwen3.5:4b") == for_model("granite4.2")
    with pytest.raises(ValueError, match="no explicit profile"):
        for_runtime_model("unknown-planner", derived_model="nuru", base_model="qwen3.5:4b")


def test_supported_profiles_reject_unverified_thinking_levels():
    with pytest.raises(ValueError, match="Unsupported thinking level 'medium'"):
        for_model("granite4.2").think("medium")


def test_a_profile_must_map_exactly_the_three_levels(tmp_path):
    path = tmp_path / "profiles.yaml"
    path.write_text('x:\n  supported: true\n  think: {"off": false, low: low}\n')
    with pytest.raises(ValueError):
        for_model("x:1b", path)


def test_off_is_read_as_a_level_not_a_yaml_boolean():
    raw = yaml.safe_load(model_profile.PROFILES.read_text())
    assert all(set(entry["think"]) == {"off", "low", "high"} for entry in raw.values() if entry["supported"])


@pytest.mark.parametrize("change", [
    {"supported": "true"}, {"unexpected": 1}, {"think": None},
    {"think": {"off": False, "low": "medium", "high": True}},
    {"default": "medium"}, {"default": False}, {"sampling": None},
    {"sampling": {"misspelled_mode": {}}},
    {"sampling": {"thinking": {"temperatur": 1}}},
    {"sampling": {"thinking": {"temperature": "0.7"}}},
    {"sampling": {"thinking": {"temperature": float("nan")}}},
    {"sampling": {"thinking": {"top_k": 1.5}}},
])
def test_malformed_supported_profiles_fail_clearly(tmp_path, change):
    entry = yaml.safe_load(model_profile.PROFILES.read_text())["qwen3.5"]
    entry.update(change)
    path = tmp_path / "profiles.yaml"
    path.write_text(yaml.safe_dump({"qwen3.5": entry}))
    with pytest.raises(ValueError):
        for_model("qwen3.5:4b", path)


def test_missing_profile_file_never_uses_a_default(tmp_path):
    with pytest.raises(FileNotFoundError):
        for_model("qwen3.5:4b", tmp_path / "missing.yaml")


def test_every_checked_in_family_has_an_explicit_support_decision():
    raw = yaml.safe_load(model_profile.PROFILES.read_text())
    shipped = {p.stem for p in model_profile.PROFILES.parent.glob("*.Modelfile")}
    claude = {"claude-haiku-5-5", "claude-sonnet-5-5"}  # API models: no Modelfile
    assert shipped | claude == set(raw) and not shipped & claude
    assert {name for name, entry in raw.items() if entry["supported"]} == {"qwen3.5", "granite4.2"} | claude


# ---------------------------------------------------------------- F13: level translation's remaining edges
def test_a_level_given_as_a_bool_passes_through_and_plain_words_translate_without_a_map_entry():
    mapped = model_profile.ModelProfile(think_map={"off": False, "low": "low", "high": "high"})
    assert mapped.think(True) is True and mapped.think(False) is False
    assert mapped.think(" FALSE ") is False and mapped.think("true") is True


def test_an_unknown_level_is_refused_with_a_map_and_passed_on_without_one():
    with pytest.raises(ValueError, match="Unsupported thinking level 'medium'"):
        model_profile.ModelProfile(think_map={"off": False, "low": "low", "high": "high"}).think("medium")
    assert model_profile.ModelProfile().think("medium") == "medium"


def test_a_profiles_file_that_is_not_a_mapping_is_refused(tmp_path):
    path = tmp_path / "profiles.yaml"
    path.write_text("- qwen3\n- granite\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a mapping of model families"):
        for_model("qwen3:8b", path)
