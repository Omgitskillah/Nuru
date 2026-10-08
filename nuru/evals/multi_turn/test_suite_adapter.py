"""Offline checks for the run_suite bridge: what reaches the run, and how diffs accumulate."""
import json
import unittest
from pathlib import Path

from mechanical_checks import assess
from suite_adapter import HERE, cumulative, export, records

CASES = json.loads((HERE / 'cases.json').read_text())


def change(path, before, after):
    return {'path': path, 'before': before, 'after': after}


class Export(unittest.TestCase):
    def test_core_cases_are_numbered_in_file_order_without_grading(self):
        exported = export(CASES)
        self.assertEqual(len(exported), 12)
        self.assertEqual(exported['101']['name'], 'MT01')
        self.assertEqual(exported['103']['name'], 'MT02b')
        self.assertNotIn('MT12', [c['name'] for c in exported.values()])
        for number, case in exported.items():
            source = CASES[case['name']]['turns']
            self.assertEqual([t['prompt'] for t in case['turns']], [t['prompt'] for t in source])
            self.assertTrue(all(set(t) == {'prompt', 'checks'} and t['checks'] == {} for t in case['turns']))

    def test_selection_keeps_numbers_and_refuses_controlled_cases(self):
        self.assertEqual(list(export(CASES, ['MT08'])), ['109'])
        with self.assertRaises(ValueError):
            export(CASES, ['MT13'])

    def test_export_loads_as_suite_cases(self):
        import sys, tempfile
        sys.path.insert(0, str(HERE.parent / 'decision_layer'))
        from cases import load_cases
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mt.json'
            path.write_text(json.dumps(export(CASES)))
            self.assertEqual(sorted(load_cases(path)), list(range(101, 113)))


class Records(unittest.TestCase):
    def test_changes_accumulate_from_the_original_fixture(self):
        turns = [{'vault_changes': [change('nuru/a.md', None, 'one')]},
                 {'vault_changes': []},
                 {'vault_changes': [change('nuru/a.md', 'one', 'two'), change('user/b.md', 'b', 'B')]},
                 {'vault_changes': [change('user/b.md', 'B', 'b')]}]
        out = cumulative(turns)
        self.assertEqual(out[1], [change('nuru/a.md', None, 'one')])
        self.assertEqual(out[2], [change('nuru/a.md', None, 'two'), change('user/b.md', 'b', 'B')])
        self.assertEqual(out[3], [change('nuru/a.md', None, 'two')])  # b.md restored: no longer a change

    def test_turns_without_evidence_screen_invalid(self):
        result = {'test': 101, 'repeat': 0, 'turns': [
            {'reply': 'NAT timeout', 'vault_changes': []},
            {'reply': 'partial', 'vault_changes': [], 'timed_out': True},
            {'not_run': True}, {'not_run': True}]}
        out = records([result], CASES)
        self.assertEqual([r['turn'] for r in out], [1, 2, 3, 4])
        statuses = [[s['status'] for s in assess(r, CASES)['screens']] for r in out]
        self.assertEqual(statuses[0], ['PASS'])
        self.assertIn('INVALID', statuses[2])
        killed = records([{'test': 101, 'timed_out': True}], CASES)
        self.assertEqual(len(killed), 4)
        self.assertTrue(all('reply' not in r and 'vault_changes' not in r for r in killed))

    def test_unknown_tests_are_skipped(self):
        self.assertEqual(records([{'test': 3, 'reply': 'x'}], CASES), [])


if __name__ == '__main__':
    unittest.main()
