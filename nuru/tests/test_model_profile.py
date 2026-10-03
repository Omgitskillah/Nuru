import pytest

from core import model_profile
from core.model_profile import ModelProfile, family, for_model


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


def test_a_family_without_a_profile_keeps_the_old_behaviour():
    unknown = for_model("llama3.1:8b")
    assert unknown == ModelProfile()
    assert unknown.think("low") == "low" and unknown.think("off") is False and unknown.think("true") is True
    assert unknown.options(True) == {}


def test_a_profile_must_map_exactly_the_three_levels(tmp_path):
    path = tmp_path / "profiles.yaml"
    path.write_text('x:\n  think: {"off": false, low: low}\n')
    with pytest.raises(ValueError):
        for_model("x:1b", path)


def test_off_is_read_as_a_level_not_a_yaml_boolean():
    import yaml
    raw = yaml.safe_load(model_profile.PROFILES.read_text())
    assert all(set(entry["think"]) == {"off", "low", "high"} for entry in raw.values())
