"""Independent explicit oracle and model-free scale timings for stewardship stages."""
from core.eval_artifacts import write_json
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess
import tempfile
import time

from obsidian_mcp.steward import Steward

FIXTURE = Path(__file__).with_name('steward_fixture.json')


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def grade(expected, rows):
    wanted = Counter(tuple(r) for r in expected)
    actual = Counter((r['namespace'], r['source'], r['rule'], r['line']) for r in rows)
    missing, extra = wanted - actual, actual - wanted
    tp = sum((wanted & actual).values())
    fp, fn = sum(extra.values()), sum(missing.values())
    return {'exact': not missing and not extra, 'tp': tp, 'fp': fp, 'fn': fn,
            'precision': tp / (tp + fp) if tp + fp else None, 'recall': tp / (tp + fn) if tp + fn else None,
            'missing': [list(k) for k in missing.elements()], 'extra': [list(k) for k in extra.elements()]}


def accuracy():
    fixture = json.loads(FIXTURE.read_text())
    with tempfile.TemporaryDirectory(prefix='nuru-steward-oracle-') as directory:
        root = Path(directory)
        for path, content in fixture['files'].items():
            p = root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        before = hashes(root)
        report = Steward(root, fixture['config']).run_once(limit=100, include_details=True)
        result = grade(fixture['oracle']['findings'], report['findings'])
        details, oracle = report['details'], fixture['oracle']
        checks = {
            'exact_note_groups': [r['paths'] for r in details['duplicates']['exact_groups']] == oracle['exact_note_groups'],
            'asset_groups': [r['paths'] for r in details['attachments']['duplicate_groups']] == oracle['asset_duplicate_groups'],
            'asset_bytes': details['attachments']['potential_duplicate_bytes'] == oracle['potential_duplicate_bytes'],
            'graph_edges': [[r['source'], r['target']] for r in details['graph']['edges']] == oracle['graph_edges'],
            'route': details['triage']['queue'][0]['destination'] == oracle['route'],
            'table_total': next(r for r in report['findings'] if r['rule'] == 'table.total')['evidence']['expected'] == oracle['table_expected_total'],
            'unchanged_files': before == hashes(root), 'complete': report['complete'], 'no_model_calls': report['model_calls'] == 0,
        }
        return {'finding_grade': result, 'checks': checks, 'pass': result['exact'] and all(checks.values()),
                'notes': report['stats']['notes'], 'assets': report['stats']['assets']}


def timing(size, repeats):
    raw = {'cold': [], 'no_change': [], 'one_note_edit': []}
    parse_counts = []
    with tempfile.TemporaryDirectory(prefix='nuru-steward-scale-') as directory:
        root = Path(directory)
        for i in range(size):
            (root / f'Note{i:05}.md').write_text(f'---\nscope: private\nowner: User\n---\n# Note {i}\n\nUnique measurement record {i}. [[Note{(i+1)%size:05}]]\n- [ ] work {i} [due:2026-10-10]\n')
        config = {'packages': ['properties', 'links', 'tasks', 'duplicates', 'graph'], 'as_of': '2026-10-08',
                  'schemas': [{'name': 'n', 'fields': {'owner': {'type': 'string', 'required': True}}}]}
        for repeat in range(repeats):
            runner = Steward(root, config)
            for stage in raw:
                if stage == 'one_note_edit':
                    p = root / 'Note00000.md'
                    p.write_text(p.read_text() + f'\nRevision {repeat}.\n')
                start = time.perf_counter()
                report = runner.run_once()
                raw[stage].append((time.perf_counter() - start) * 1000)
                if report['finding_count'] or not report['complete']:
                    raise AssertionError('Unexpected scale-vault findings/diagnostics')
                if stage == 'one_note_edit':
                    parse_counts.append({'notes': report['stats']['parsed_notes'], 'links': report['stats']['link_parsed_notes']})
            print(f'scale {size}: repeat {repeat+1}/{repeats}', flush=True)
    return {'notes': size, 'repeats': repeats, 'packages': config['packages'], 'raw_ms': raw,
            'median_ms': {k: round(statistics.median(v), 3) for k, v in raw.items()}, 'one_edit_parse_counts': parse_counts,
            'model_calls': 0}


def main():
    import resource  # POSIX only, and only for the run; tests import this module on Windows too
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', default='1000,10000')
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--hardware-note', default='Not supplied; see platform metadata')
    args = parser.parse_args()
    sizes = [int(v) for v in args.sizes.split(',')]
    if args.repeats < 1 or any(v < 2 for v in sizes):
        parser.error('Positive repeats and sizes >=2 required')
    result = {'fixture_sha256': hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), 'accuracy': accuracy(),
              'platform': platform.platform(), 'python': platform.python_version(),
              'hardware_note': args.hardware_note,
              'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                  [Path(__file__), *sorted(Path('servers/obsidian/src/obsidian_mcp').glob('steward*.py'))]},
              'dependency_commit': subprocess.check_output(['git', '-C', 'servers/obsidian', 'rev-parse', 'HEAD'], text=True).strip(),
              'nuru_source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'timing_scope': 'Steward.run_once, including hashing, parsing/reconciliation, feature computation, report snapshot; excludes fixture creation and JSON serialization. Cold parser cache, OS cache not flushed.',
              'timings': [timing(n, args.repeats) for n in sizes],
              'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if platform.system() == 'Darwin' else 1024),
              'live_models': 'NOT RUN', 'human_efficacy': 'NOT RUN'}
    write_json(args.output, result)
    print(json.dumps({'accuracy': result['accuracy'], 'output': str(args.output)}, ensure_ascii=False))
    if not result['accuracy']['pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
