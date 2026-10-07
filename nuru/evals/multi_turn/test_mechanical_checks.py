"""Offline regression checks for the screening boundaries, not model-quality tests."""
import unittest
from mechanical_checks import assess, screen


class Screens(unittest.TestCase):
    def test_missing_diff_is_not_unchanged(self):
        self.assertEqual(screen({'kind': 'markdown_unchanged'}, {}), 'INVALID')

    def test_markdown_mutation_fails_but_index_change_does_not(self):
        check = {'kind': 'markdown_unchanged'}
        self.assertEqual(screen(check, {'vault_changes': [{'path': 'user/a.md'}]}), 'FAIL')
        self.assertEqual(screen(check, {'vault_changes': [{'path': '.obsidian-mcp-index.sqlite3'}]}), 'PASS')

    def test_nested_and_fenced_bullets_are_not_top_level_bullets(self):
        reply = '- first\n  - nested\n- second\n```text\n- code\n```\n'
        self.assertEqual(screen({'kind': 'bullet_count', 'expected': 2}, {'reply': reply}), 'PASS')
        self.assertEqual(screen({'kind': 'bullet_count', 'expected': 3}, {'reply': reply}), 'FAIL')

    def test_missing_reply_is_invalid(self):
        self.assertEqual(screen({'kind': 'nonempty_line_count', 'expected': 2}, {}), 'INVALID')

    def test_blank_lines_not_counted(self):
        self.assertEqual(screen({'kind': 'nonempty_line_count', 'expected': 2}, {'reply': 'Bob\n\nAlice\n'}), 'PASS')

    def test_bad_turn_is_invalid(self):
        self.assertEqual(assess({'case_id': 'X', 'turn': 0}, {'X': {'turns': []}})['status'], 'INVALID')


if __name__ == '__main__':
    unittest.main()
