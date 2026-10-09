"""Ensure the checked-in evaluator oracle stays aligned with the feature."""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / 'evals' / 'vault_steward' / 'wp02_eval.py'
_SPEC = importlib.util.spec_from_file_location('wp02_eval', _PATH)
evaluator = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(evaluator)


def test_curated_fixture_is_exact_and_read_only():
    result = evaluator.accuracy()
    assert result['exact_occurrence_match']
    assert result['complete']
    assert result['unchanged_files']
    assert (result['tp'], result['fp'], result['fn']) == (5, 0, 0)
    assert result['counts']['ambiguous'] == 2
    assert result['model_calls'] == 0


def test_grader_rejects_false_flags_missing_occurrences_and_wrong_reasons():
    expected = [{'source': 'Source.md', 'line': 1, 'status': 'broken', 'reason': 'missing_heading'},
                {'source': 'Source.md', 'line': 2, 'status': 'resolved', 'reason': 'file_exists'}]
    predicted = [{'source': 'Source.md', 'line': 1, 'status': 'broken', 'reason': 'missing_file'},
                 {'source': 'Source.md', 'line': 2, 'status': 'broken', 'reason': 'missing_file'}]
    result = evaluator.grade(expected, predicted)
    assert not result['exact_occurrence_match']
    assert result['broken_precision'] == 0.5
    assert not evaluator.grade(expected, [])['exact_occurrence_match']
    assert evaluator.grade([], [])['broken_precision'] is None
