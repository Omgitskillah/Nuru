"""Approved Nomic v2-only live index acceptance on isolated copies of both vaults."""
from core.eval_artifacts import write_json
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import struct
import tempfile
import time
import urllib.request

from obsidian_mcp import semantic
from validation_fixture import ORACLE

MODEL = 'nomic-embed-text-v2-moe:latest'
HOST = 'http://localhost:11434'
BENCHMARK_QUERIES = [
    {'query': 'What correction explains the 47-minute modem disconnect as a network NAT timeout?', 'paths': ['user/correction-the-47-minute-mystery.md']},
    {'query': 'Which debugging session investigates UPSV and DTR preventing modem power saving?', 'paths': ['shared/debugging-session-psm-not-engaging.md']},
    {'query': 'Where are FreeRTOS task priorities and stack high-water marks recorded?', 'paths': ['nuru/Topics/freertos-task-management.md']},
    {'query': 'Which topic discusses the Berlin startup ecosystem?', 'paths': ['nuru/Topics/berlin-startup-ecosystem.md']},
    {'query': 'Which revised battery calculation includes self-discharge and periodic attach retry costs?', 'paths': ['nuru/battery-life-modeling-revised.md']},
]


def get(endpoint):
    with urllib.request.urlopen(HOST + endpoint, timeout=10) as response:
        return json.load(response)


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def vector_state(conn):
    # Compare by chunk identity, not allocation-dependent row IDs.
    return {(p, t): struct.unpack(f'{len(b)//4}f', b) for p, t, b in conn.execute(
        'SELECT chunks.path,chunks.text,vectors.embedding FROM chunks JOIN vectors ON vectors.rowid=chunks.id')}


def evaluate(source, queries, repeats, revision, output, report):
    original = hashes(source)
    lane = {'vault': source.name, 'queries': queries, 'source_hashes': original, 'runs': []}
    report['lanes'].append(lane)
    for repeat in range(repeats):
        with tempfile.TemporaryDirectory(prefix='nuru-nomic-v2-') as temporary:
            # Resolve macOS /var -> /private/var before the scanner's containment check.
            root = (Path(temporary).resolve() / 'vault')
            shutil.copytree(source, root)
            cfg = semantic.load_config(root, {'OLLAMA_HOST': HOST, 'OBSIDIAN_EMBED_MODEL': MODEL,
                'OBSIDIAN_EMBED_DIGEST': revision, 'OBSIDIAN_CHUNK_CHARS': '400',
                'OBSIDIAN_EMBED_BATCH_CHUNKS': '2', 'OBSIDIAN_INDEX_EXCLUSIONS': '["Maintenance"]'})
            sample = {'repeat': repeat+1, 'checks': {}, 'timings_ms': {}, 'embedding_requests': []}
            lane['runs'].append(sample)
            live = semantic.ollama_embedder(cfg)

            def embed(texts):
                start = time.perf_counter()
                vectors = live(texts)
                sample['embedding_requests'].append({'texts': len(texts), 'ms': (time.perf_counter()-start)*1000})
                return vectors

            def timed(name, operation):
                start = time.perf_counter()
                value = operation()
                sample['timings_ms'][name] = (time.perf_counter()-start)*1000
                return value

            try:
                with semantic.connect(cfg) as conn:
                    eligible = sorted(p.relative_to(root).as_posix() for p in semantic.vault_notes(root, cfg.exclusions))
                    if not eligible:
                        raise ValueError('INVALID setup: no eligible notes')
                    cold = timed('cold_index', lambda: semantic.refresh(conn, cfg, embed, force_hash=True))
                    sample['checks']['indexed_paths'] = sorted(cold.embedded) == eligible
                    sample['retrieval'] = []
                    for item in queries:
                        hits = timed('query_' + str(len(sample['retrieval'])), lambda: semantic.search(conn, cfg, embed, item['query'], 10))
                        rank = next((i+1 for i, hit in enumerate(hits) if hit['path'] in item['paths']), None)
                        sample['retrieval'].append({**item, 'first_expected_chunk_rank': rank, 'hits': hits})
                    count = len(sample['embedding_requests'])
                    timed('no_change', lambda: semantic.refresh(conn, cfg, embed, force_hash=True))
                    sample['checks']['no_change_zero_calls'] = count == len(sample['embedding_requests'])
                    edit_path = root / eligible[0]
                    stat = edit_path.stat()
                    edit_path.write_text(edit_path.read_text() + '\nAn additional field observation was recorded.\n')
                    os.utime(edit_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                    changed = timed('same_mtime_edit', lambda: semantic.refresh(conn, cfg, embed, force_hash=True))
                    sample['checks']['edit_only_changed_note'] = changed.embedded == [eligible[0]]
                    moved = root / 'moved' / edit_path.name
                    moved.parent.mkdir()
                    edit_path.rename(moved)
                    count = len(sample['embedding_requests'])
                    move = timed('rename', lambda: semantic.refresh(conn, cfg, embed, force_hash=True))
                    sample['checks']['rename_reuses_vectors'] = move.moved == [(eligible[0], 'moved/' + edit_path.name)] and count == len(sample['embedding_requests'])
                    moved.unlink()
                    count = len(sample['embedding_requests'])
                    deletion = timed('deletion', lambda: semantic.refresh(conn, cfg, embed, force_hash=True))
                    sample['checks']['deletion_zero_calls'] = deletion.removed == ['moved/' + edit_path.name] and count == len(sample['embedding_requests'])
                    incremental = vector_state(conn)
                    semantic.reset(conn)
                    timed('full_rebuild', lambda: semantic.refresh(conn, cfg, embed, force_hash=True))
                    rebuilt = vector_state(conn)
                    error = max((abs(a-b) for key in incremental if key in rebuilt for a,b in zip(incremental[key],rebuilt[key])), default=0)
                    sample['max_vector_error'] = error
                    sample['checks']['rebuild_equivalence'] = incremental.keys() == rebuilt.keys() and error <= 1e-5
                    (root / 'queued.md').write_text('New queue entry: a lighthouse schedules coastal navigation signals.')
                    count = len(sample['embedding_requests'])
                    pause = semantic.refresh(conn, cfg, embed, force_hash=True, should_stop=lambda: True)
                    sample['checks']['pause_zero_calls'] = pause.interrupted and pause.pending == ['queued.md'] and count == len(sample['embedding_requests'])
                with semantic.connect(cfg) as conn:
                    resume = timed('restart_resume', lambda: semantic.refresh(conn, cfg, embed, force_hash=True))
                    sample['checks']['restart_resumes'] = resume.embedded == ['queued.md'] and not semantic.maintenance_status(conn,cfg)['pending']
                    try:
                        semantic.refresh(conn, replace(cfg, model_digest='wrong-digest'), embed)
                    except ValueError:
                        sample['checks']['digest_rejected'] = True
                    else:
                        sample['checks']['digest_rejected'] = False
                    sample['status'] = semantic.maintenance_status(conn,cfg)
                sample['checks']['batch_bound'] = all(r['texts'] <= 2 for r in sample['embedding_requests'])
                sample['pass'] = all(sample['checks'].values())
            except Exception as exc:
                sample['pass'] = False
                sample['error'] = f'{type(exc).__name__}: {exc}'
            write_json(output, report)
            print(json.dumps({'vault': source.name, 'repeat': repeat+1, 'pass': sample['pass'],
                              'requests': len(sample['embedding_requests']), 'error': sample.get('error')}), flush=True)
            if not sample['pass']:
                break
    lane['source_unchanged'] = original == hashes(source)
    samples = lane['runs']
    lane['summary'] = {'pass': len(samples) == repeats and all(r['pass'] for r in samples) and lane['source_unchanged'],
                       'top1': sum(r['first_expected_chunk_rank'] == 1 for s in samples for r in s.get('retrieval',[])),
                       'top3': sum(r['first_expected_chunk_rank'] is not None and r['first_expected_chunk_rank'] <= 3 for s in samples for r in s.get('retrieval',[])),
                       'top10': sum(r['first_expected_chunk_rank'] is not None for s in samples for r in s.get('retrieval',[])),
                       'query_count': sum(len(s.get('retrieval',[])) for s in samples),
                       'median_timings_ms': {k: statistics.median(s['timings_ms'][k] for s in samples if k in s['timings_ms']) for k in samples[0]['timings_ms']}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--repeats', type=int, choices=range(1,6), default=5)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    tag = next(m for m in get('/api/tags')['models'] if m['name'] == MODEL)
    report = {'model': MODEL, 'digest': tag['digest'], 'ollama': get('/api/version'),
              'platform': platform.platform(), 'chunk_chars': 400, 'batch_chunks': 2, 'truncate': False,
              'embedding_token_usage': 'NOT COLLECTED by the production embedder wrapper',
              'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'semantic_sha256': hashlib.sha256(Path(semantic.__file__).read_bytes()).hexdigest(), 'lanes': []}
    root = args.root.resolve()
    evaluate(root / 'VaultStewardValidation', ORACLE['retrieval'], args.repeats, tag['digest'], args.output, report)
    if report['lanes'][0]['summary']['pass']:
        evaluate(root / 'DummyVaultValidation', BENCHMARK_QUERIES, args.repeats, tag['digest'], args.output, report)
    write_json(args.output, report)
    print(json.dumps({r['vault']: r['summary'] for r in report['lanes']}))
    raise SystemExit(0 if len(report['lanes']) == 2 and all(r['summary']['pass'] for r in report['lanes']) else 1)
