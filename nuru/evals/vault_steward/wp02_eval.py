"""Offline accuracy and cold/incremental timing. Never reads the real vault."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import statistics
import tempfile
import time

from obsidian_mcp.link_integrity import LinkIndex, Policy

FIXTURE = Path(__file__).with_name('wp02_fixture.json')


def materialize(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def grade(expected, predicted):
    expected_rows = Counter((r['source'], r['line'], r['status'], r['reason']) for r in expected)
    predicted_rows = Counter((r['source'], r['line'], r['status'], r['reason']) for r in predicted)
    missing, extra = expected_rows - predicted_rows, predicted_rows - expected_rows
    truth = Counter((r['source'], r['line']) for r in expected if r['status'] == 'broken')
    found = Counter((r['source'], r['line']) for r in predicted if r['status'] == 'broken')
    tp = sum((truth & found).values())
    fp = sum((found - truth).values())
    fn = sum((truth - found).values())
    return {'exact_occurrence_match': not missing and not extra,
            'missing': [list(k) + [n] for k, n in missing.items()],
            'extra': [list(k) + [n] for k, n in extra.items()],
            'broken_precision': tp / (tp + fp) if tp + fp else None,
            'broken_recall': tp / (tp + fn) if tp + fn else None,
            'tp': tp, 'fp': fp, 'fn': fn}


def accuracy():
    fixture = json.loads(FIXTURE.read_text())
    with tempfile.TemporaryDirectory(prefix='nuru-wp02-fixture-') as directory:
        root = Path(directory)
        materialize(root, fixture['files'])
        before = hashes(root)
        policy = Policy(tuple(fixture['policy']['exclusions']), tuple((x['source'], x['target']) for x in fixture['policy']['intentional']))
        report = LinkIndex(root, policy).report(include_resolved=True, limit=100)
        outcome = grade(fixture['expected'], report['references'])
        outcome.update(counts=report['counts'], complete=report['complete'],
                       unchanged_files=before == hashes(root), model_calls=report['stats']['model_calls'])
        return outcome


def median(values):
    return {'median_ms': round(statistics.median(values), 3),
            'min_ms': round(min(values), 3), 'max_ms': round(max(values), 3)}


def timing(size, repeats):
    cold, no_change, edit, incoming = [], [], [], []
    parsed = []
    with tempfile.TemporaryDirectory(prefix='nuru-wp02-scale-') as directory:
        root = Path(directory)
        # Unique medium-length text; every source links to the next note, its
        # heading and block, and a common missing target. Content is synthetic.
        for i in range(size):
            target = (i + 1) % size
            materialize(root, {f'Note{i:05}.md': f'# Section {i}\n\nEvidence for synthetic record {i}. ^record-{i}\n\n'
                f'[[Note{target:05}]]\n[[Note{target:05}#Section {target}]]\n[[Note{target:05}#^record-{target}]]\n[[Absent]]\n\n'
                + (f'Record {i} has unique bookkeeping context. ' * 20) + '\n'})
        target_file = root / 'Note00000.md'
        baseline = target_file.read_text()
        for repeat in range(repeats):
            target_file.write_text(baseline)
            index = LinkIndex(root)
            start = time.perf_counter()
            index.report()
            cold.append((time.perf_counter() - start) * 1000)
            start = time.perf_counter()
            index.report()
            no_change.append((time.perf_counter() - start) * 1000)
            target_file.write_text(baseline + f'\nUpdated revision {repeat}.\n')
            start = time.perf_counter()
            report = index.report()
            edit.append((time.perf_counter() - start) * 1000)
            parsed.append(report['stats']['parsed_notes'])
            start = time.perf_counter()
            result = index.report(target_path='Note00000.md')
            incoming.append((time.perf_counter() - start) * 1000)
            assert result['count'] == 3
            assert report['counts']['resolved'] == 3 * size
            assert report['counts']['broken'] == size
    return {'notes': size, 'repeats': repeats, 'cold': median(cold), 'no_change': median(no_change),
            'one_note_edit': median(edit), 'incoming_including_reconciliation': median(incoming),
            'parsed_after_one_note_edit': parsed, 'model_calls': 0,
            'raw_ms': {'cold': cold, 'no_change': no_change, 'one_note_edit': edit, 'incoming': incoming}}


def main():
    import resource  # POSIX only, and only for the run; tests import this module on Windows too
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', default='1000,10000')
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    sizes = [int(x) for x in args.sizes.split(',')]
    if args.repeats < 1 or any(n < 2 for n in sizes):
        parser.error('repeats must be positive and sizes at least 2')
    results = {'fixture_sha256': hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
               'platform': platform.platform(), 'python': platform.python_version(),
               'timing_scope': 'LinkIndex.report wall time including reconciliation and snapshot hashing; no stdout serialization or model calls',
               'accuracy': accuracy(), 'timings': [timing(n, args.repeats) for n in sizes],
               'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if platform.system() == 'Darwin' else 1024)}
    serialized = json.dumps(results, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(serialized)
    print(serialized)
    if not results['accuracy']['exact_occurrence_match'] or not results['accuracy']['unchanged_files']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
