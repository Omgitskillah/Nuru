"""Safeguard settings validate without a model package or calibration metadata."""
import json
from pathlib import Path

import pytest

from core import decision_config
from core.decision_config import DecisionConfig, load_config


def test_shipped_safeguards_and_defaults_are_preserved():
    config = load_config()
    assert config.decision_points == {
        "step_cap": "enforce", "write_checks": "enforce", "watch_thinking": "enforce",
    }
    assert config.tool_gate.step_cap == 10
    assert config.tiers.enforce is False
    assert config.database_path == decision_config.REPO_ROOT / config.database


@pytest.mark.parametrize("change", ["unknown", "missing", "zero_cap", "legacy_model"])
def test_invalid_or_retired_configuration_fails_before_a_session_starts(change):
    raw = load_config().model_dump(mode="json")
    if change == "unknown":
        raw["decision_points"]["not_a_safeguard"] = "enforce"
    elif change == "missing":
        del raw["decision_points"]["write_checks"]
    elif change == "zero_cap":
        raw["tool_gate"]["step_cap"] = 0
    else:
        raw["laya"] = {"offline": True}
    with pytest.raises(ValueError):
        DecisionConfig.model_validate(raw)


def test_explicit_config_keeps_absolute_database_location(tmp_path):
    raw = load_config().model_dump(mode="json")
    raw["database"] = str(tmp_path / "previous-decisions.sqlite3")
    raw["decision_points"]["watch_thinking"] = "shadow"
    path = tmp_path / "settings.yaml"
    path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_config(path)
    assert config.database_path == Path(raw["database"])
    assert config.mode("watch_thinking") == "shadow"
    assert config.mode("removed_model_point") == "off"
