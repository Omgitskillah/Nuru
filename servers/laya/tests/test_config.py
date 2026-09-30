import copy

import pytest
import yaml

from laya_mcp.config import CONFIG_PATH, DecisionConfig, load_config


@pytest.fixture
def raw():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def test_the_shipped_config_enforces_only_points_decided_by_code():
    config = load_config()
    enforced = {point for point, mode in config.decision_points.items() if mode == "enforce"}
    assert enforced <= {"step_cap", "write_checks", "watch_thinking"}


def test_a_point_decided_by_code_can_be_enforced_before_any_calibration(raw):
    for point in ("step_cap", "write_checks", "watch_thinking"):
        raw["decision_points"][point] = "enforce"
    assert DecisionConfig.model_validate(raw).mode("write_checks") == "enforce"


def test_the_watchs_laya_question_still_needs_calibration(raw):
    raw["decision_points"]["watch_laya"] = "enforce"
    with pytest.raises(ValueError, match="watch_laya is set to enforce"):
        DecisionConfig.model_validate(raw)


def test_enforce_is_refused_while_a_threshold_is_uncalibrated(raw):
    raw["decision_points"]["gate_write"] = "enforce"
    with pytest.raises(ValueError, match="gate_write is set to enforce.*gate_write.verdict.proceed"):
        DecisionConfig.model_validate(raw)


def test_enforce_needs_held_out_labels_not_just_a_date(raw):
    raw["decision_points"]["gate_write"] = "enforce"
    for options in raw["thresholds"]["gate_write"].values():
        for threshold in options.values():
            threshold["calibration"] = {"date": "2026-10-01", "labels": 200, "held_out": 0}
    with pytest.raises(ValueError, match="not been calibrated"):
        DecisionConfig.model_validate(raw)


def test_enforce_is_allowed_once_every_threshold_it_uses_is_calibrated(raw):
    raw["decision_points"]["gate_write"] = "enforce"
    for options in raw["thresholds"]["gate_write"].values():
        for threshold in options.values():
            threshold["calibration"] = {"date": "2026-10-01", "labels": 200, "held_out": 50}
    assert DecisionConfig.model_validate(raw).mode("gate_write") == "enforce"


def test_rerank_has_no_thresholds_so_nothing_blocks_enforcing_it(raw):
    raw["decision_points"]["rerank"] = "enforce"
    assert DecisionConfig.model_validate(raw).mode("rerank") == "enforce"


def test_every_decision_point_must_be_listed(raw):
    bad = copy.deepcopy(raw)
    del bad["decision_points"]["rerank"]
    with pytest.raises(ValueError, match="missing"):
        DecisionConfig.model_validate(bad)
    bad = copy.deepcopy(raw)
    bad["decision_points"]["telepathy"] = "shadow"
    with pytest.raises(ValueError, match="unknown"):
        DecisionConfig.model_validate(bad)


def test_an_option_without_its_own_threshold_uses_the_questions_any():
    config = load_config()
    assert config.threshold("classify_note_type", "note_type", "person") == 0.70
    assert config.threshold("tool_gate_start", "mode", "read") is None


def test_off_stays_a_string_not_a_yaml_boolean():
    config = load_config()
    assert config.tiers.order[0] == "off"
    assert config.tiers.models["granite4.2:3b"]["off"] is False
    assert config.threshold("assess_effort", "tier", "off") == 0.90


def test_the_database_path_is_relative_to_the_repo_root():
    config = load_config()
    assert config.database_path.parts[-3:] == ("nuru", ".db", "decisions.sqlite3")
    assert config.database_path.is_absolute()
