import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'evals/vault_steward/steward_eval.py'
spec = importlib.util.spec_from_file_location('steward_eval_fixture', PATH)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_explicit_oracle_and_authored_bytes():
    result = evaluation.accuracy()
    assert result['pass'], result
    assert result['finding_grade']['tp'] == 11


def test_grade_rejects_wrong_rule_extra_and_missing():
    expected = [['properties', 'a.md', 'property.type', 2]]
    wrong = [{'namespace': 'properties', 'source': 'a.md', 'rule': 'property.value', 'line': 2}]
    result = evaluation.grade(expected, wrong)
    assert not result['exact'] and result['fp'] == result['fn'] == 1
