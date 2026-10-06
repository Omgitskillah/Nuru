import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def score_runs(monkeypatch):
    evals = Path(__file__).resolve().parents[1] / "evals" / "decision_layer"
    monkeypatch.syspath_prepend(str(evals))
    spec = importlib.util.spec_from_file_location("score_runs_32_test", evals / "score_runs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("reply", [
    "Yes on paper, but only under periodic attach (architecture B) at about 1 report/hour. Always-connected cannot reach 3 years.",
    "**Bottom line:** the 3-year target is reachable only with periodic attach, and only if the alarm latency allows it.",
    "- **Can we still hit 3 years?** Only in the periodic-attach architecture (B), at a modest reporting rate.",
])
def test_a_hedged_yes_via_periodic_attach_counts(score_runs, reply):
    assert score_runs._periodic_attach_verdict(reply)


@pytest.mark.parametrize("reply", [
    "No. The periodic attach model only works if PSM actually engages, and it doesn't.",
    "The 3-year requirement is only met in the theoretical periodic attach scenario, which is currently unachievable.",
    "We cannot meet the 3-year requirement. The periodic attach model (13.6 years) relies on PSM working.",
    "## Can we hit 3 years?\n- **Yes**, if:\n  - The device uses periodic attach.\n  - PSM eventually works (UART fix pending).\n",
    "Periodic attach gives 13.6 years, which meets the target. But the requirement is not achievable while PSM is broken.",
    "With PSM functioning correctly (as required by Architecture B), the device meets the 3-year requirement.",
])
def test_a_no_or_a_yes_that_needs_psm_does_not(score_runs, reply):
    assert not score_runs._periodic_attach_verdict(reply)


def test_the_biggest_risk_must_be_the_report_rate_or_alarm_latency(score_runs):
    assert score_runs._biggest_risk_32("**Biggest remaining risk:** the unconfirmed alarm-latency requirement.")
    assert score_runs._biggest_risk_32("The single biggest risk is the report rate: 6 an hour gives 2.3 years.")
    assert not score_runs._biggest_risk_32("The single biggest remaining risk is PSM not engaging.")
    assert not score_runs._biggest_risk_32("At 6 reports/hour life drops to 2.3 years.")  # no risk statement


# Regression cases from the PR #4 review (2026-10-06).
@pytest.mark.parametrize("reply", [
    "Periodic attach does not meet the 3-year requirement.",
    "Periodic attach doesn't meet the 3-year requirement.",
    "Architecture B fails to meet the 3-year target once retries are counted.",
])
def test_an_explicit_does_not_meet_is_not_a_yes(score_runs, reply):
    assert not score_runs._periodic_attach_verdict(reply)


def test_a_qualified_estimate_is_still_a_yes(score_runs):
    assert score_runs._periodic_attach_verdict(
        "Periodic attach meets the requirement without PSM; this is a theoretical estimate pending hardware validation.")


@pytest.mark.parametrize("reply", [
    "The biggest risk is PSM not engaging. Separately, at 6 reports/hour battery life is 2.3 years.",
    "The biggest risk is unmeasured standby current; report rate and alarm latency are secondary risks.",
])
def test_the_risk_must_be_the_one_named_biggest(score_runs, reply):
    assert not score_runs._biggest_risk_32(reply)


def test_a_wrong_audit_that_mentions_the_right_numbers_does_not_score_full_marks(score_runs):
    reply = ("Periodic attach does not meet the 3-year requirement. Always-connected lasts 237 days. "
             "The biggest risk is PSM not engaging. Separately, at 6 reports/hour life is 2.3 years. Keepalive is 600 s.")
    points, checks = score_runs.score({"test": 32, "reply": reply, "tool_calls": [], "executed": None, "vault_changes": []})
    assert dict(checks)["periodic attach meets it, without PSM"] is False
    assert dict(checks)["biggest risk: report rate or alarm latency"] is False
    assert points == 3
