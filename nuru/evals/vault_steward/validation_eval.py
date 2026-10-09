"""Grade the explicit fixture and inventory the benchmark copy without invented labels."""
import argparse
import json
from pathlib import Path
import time

from obsidian_mcp.steward import Steward
from steward_eval import grade, hashes
from validation_fixture import CONFIG, ORACLE


def main(root, output):
    fixture, copy = root / 'VaultStewardValidation', root / 'DummyVaultValidation'
    before = hashes(fixture)
    runner = Steward(fixture, CONFIG)
    start = time.perf_counter()
    report = runner.run_once(limit=100, include_details=True)
    elapsed = (time.perf_counter()-start)*1000
    finding_grade = grade(ORACLE['findings'], report['findings'])
    details = report['details']
    checks = {
        'complete': report['complete'], 'model_calls_zero': report['model_calls'] == 0,
        'exact_groups': sorted(r['paths'] for r in details['duplicates']['exact_groups']) == ORACLE['exact_note_groups'],
        'asset_groups': sorted(r['paths'] for r in details['attachments']['duplicate_groups']) == ORACLE['asset_groups'],
        'asset_bytes': details['attachments']['potential_duplicate_bytes'] == ORACLE['potential_duplicate_bytes'],
        'graph_edges': sorted([[r['source'], r['target']] for r in details['graph']['edges']]) == ORACLE['graph_edges'],
        'claims': {r['entity']: r['classification'] for r in details['claims']['comparisons']} == ORACLE['claim_classifications'],
        'table_total': next((r['evidence']['expected'] for r in report['findings'] if r['rule'] == 'table.total'), None) == '17.50',
        'triage_protected': all(r['decision'] == 'leave_in_inbox' for r in details['triage']['queue'] if r['source'] != 'Inbox/route.md'),
    }
    unchanged = runner.run_once(limit=100)
    checks['no_change_same_findings'] = grade(ORACLE['findings'], unchanged['findings'])['exact']
    checks['source_bytes_unchanged'] = before == hashes(fixture)
    copy_before = hashes(copy)
    copy_config = {'packages': ['properties', 'links', 'attachments', 'tasks', 'duplicates', 'graph'],
                   'as_of': '2026-10-09', 'timezone': 'Europe/Berlin'}
    inventory = Steward(copy, copy_config).run_once(limit=100, include_details=True)
    result = {'fixture': {'grade': finding_grade, 'checks': checks, 'wall_ms': elapsed, 'report': report,
                          'pass': finding_grade['exact'] and all(checks.values())},
              'benchmark_copy': {'grading': 'UNLABELED coverage scan; findings are not measured true defects',
                                 'config': copy_config, 'report': inventory, 'unchanged': copy_before == hashes(copy)},
              'model_calls': 0}
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps({'fixture': finding_grade, 'checks': checks, 'benchmark_findings': inventory['finding_count'],
                      'benchmark_complete': inventory['complete']}))
    raise SystemExit(0 if result['fixture']['pass'] and result['benchmark_copy']['unchanged'] else 1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    main(args.root.resolve(), args.output)
