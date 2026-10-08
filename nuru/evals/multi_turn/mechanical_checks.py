"""Deterministic screens for normalized per-turn records; never an acceptance grader.

Input JSON list: {case_id, turn, reply, vault_changes}; missing evidence is INVALID.
The adapter must supply actual cumulative-to-turn Markdown diffs from snapshots.
"""
import argparse
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def screen(check, record):
    kind = check['kind']
    if kind == 'markdown_unchanged':
        changes = record.get('vault_changes')
        if not isinstance(changes, list) or any(not isinstance(c, dict) or not isinstance(c.get('path'), str) for c in changes):
            return 'INVALID'
        return 'FAIL' if any(c['path'].endswith('.md') for c in changes) else 'PASS'
    reply = record.get('reply')
    if not isinstance(reply, str):
        return 'INVALID'
    if kind == 'bullet_count':
        # A screen only: prose outside the bullets and semantic adequacy need review.
        prose = re.sub(r'(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*$', '', reply)
        count = len(re.findall(r'^[-*+]\s+\S', prose, re.M))
    elif kind == 'nonempty_line_count':
        count = sum(bool(line.strip()) for line in reply.splitlines())
    else:
        return 'INVALID'
    return 'PASS' if count == check['expected'] else 'FAIL'


def assess(record, cases):
    case = cases.get(record.get('case_id'))
    number = record.get('turn')
    if case is None or type(number) is not int or not 1 <= number <= len(case['turns']):
        return {'case_id': record.get('case_id'), 'turn': number, 'status': 'INVALID', 'reason': 'Unknown case or turn'}
    checks = case['turns'][number - 1].get('mechanical_checks', [])
    return {'case_id': record['case_id'], 'turn': number,
            'screens': [{'check': check, 'status': screen(check, record)} for check in checks],
            'human_review_required': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', type=Path, required=True)
    args = parser.parse_args()
    records = json.loads(args.records.read_text())
    if not isinstance(records, list):
        parser.error('records must be a JSON list')
    cases = json.loads((HERE / 'cases.json').read_text())
    print(json.dumps([assess(record, cases) for record in records], indent=2))


if __name__ == '__main__':
    main()
