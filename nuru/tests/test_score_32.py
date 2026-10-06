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


# Second round from the PR #4 review.
def test_a_negated_risk_is_not_the_stated_risk(score_runs):
    assert not score_runs._biggest_risk_32(
        "The biggest risk is not the report rate or alarm latency; it is unmeasured standby current.")
    assert score_runs._biggest_risk_32("The biggest risk is the report rate, not PSM.")


@pytest.mark.parametrize("reply", [
    "Periodic attach meets the requirement, whereas always-connected does not meet it.",
    "Periodic attach meets the requirement; always-connected does not meet it.",
    "Architecture B meets 3 years with margin, while architecture A fails it by 5x.",
])
def test_a_denial_about_always_connected_does_not_deny_periodic_attach(score_runs, reply):
    assert score_runs._periodic_attach_verdict(reply)


@pytest.mark.parametrize("reply", [
    "Periodic attach meets the 3-year target on paper.\n\nCan we hit 3 years?\nNo, not yet.\nYou must fix PSM engagement first.",
    "Periodic attach passes on paper.\n\n**No, not yet.**",
    "Periodic attach passes on paper.\n\n**The Path to 3 Years:** You must fix PSM engagement.",
    "Periodic attach passes on paper. You can't hit 3 years with PSM off.",
])
def test_a_later_overall_no_or_psm_on_the_path_to_3_years_is_not_a_yes(score_runs, reply):
    # From the PR #5 paired runs (2026-10-06): an early "B passes", then "No, not yet ... fix PSM".
    assert not score_runs._periodic_attach_verdict(reply)


def test_not_yet_demonstrated_is_still_a_yes(score_runs):
    assert score_runs._periodic_attach_verdict(
        "Yes on paper: periodic attach meets 3 years with margin, though it is not yet demonstrated on hardware.")


def test_the_saved_reply_from_the_paired_runs_is_not_a_yes(score_runs):
    reply = (Path(__file__).parent / "fixtures" / "test32-paired-candidate-2273a3a2.md").read_text()
    assert not score_runs._periodic_attach_verdict(reply)
    assert not score_runs._biggest_risk_32(reply)


def test_a_denial_about_periodic_attach_in_a_later_clause_still_counts(score_runs):
    assert not score_runs._periodic_attach_verdict(
        "Periodic attach meets the target on paper, but it relies on PSM working, which it doesn't.")
