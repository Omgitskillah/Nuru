"""Reproduce review overhead checks with disposable notes and fake embeddings."""
import hashlib
import json
from pathlib import Path
import tempfile
import time

from obsidian_mcp import semantic
from core.eval_artifacts import read_json, write_json


def main(output):
    with tempfile.TemporaryDirectory(prefix='steward-review-overhead-') as temporary:
        root = Path(temporary).resolve()
        vault = root / 'vault'
        vault.mkdir()
        for n in range(2000):
            (vault / f'{n:04}.md').write_text(f'Note {n}\n' + 'information ' * 350)
        config = semantic.Config(vault, root / 'index.db', 'fake', 1500, 'http://unused')
        def embed(texts):
            return [[1., .5, float(len(t) % 7)] for t in texts]
        conn = semantic.connect(config)
        original = semantic._read_note
        try:
            semantic.refresh(conn, config, embed)
            reads, statements, rows = [], [], []
            semantic._read_note = lambda root, path: reads.append(path.name) or original(root, path)
            conn.set_trace_callback(statements.append)
            for repeat in range(5):
                reads.clear()
                statements.clear()
                started = time.perf_counter()
                result = semantic.refresh(conn, config, embed)
                rows.append({'repeat': repeat + 1, 'ms': round((time.perf_counter()-started)*1000, 2),
                             'reads': len(reads), 'note_updates': sum(s.startswith('UPDATE notes') for s in statements),
                             'dml': sum(s.startswith(('INSERT', 'UPDATE', 'DELETE')) for s in statements),
                             'embedded': result.embedded})
        finally:
            semantic._read_note = original
            conn.close()
    directory = Path(__file__).parent
    manifest = read_json(directory / 'artifact-compression-manifest.json')
    for item in manifest:
        import gzip
        raw = gzip.decompress((directory / item['file']).read_bytes())
        assert hashlib.sha256(raw).hexdigest() == item['sha256']
    report = {'model_calls': 0, 'notes': 2000, 'bytes_per_note_approx': 4200, 'rolling_cap': 100,
              'passes': rows, 'all_archived_hashes_match': True, 'compression_manifest_files': len(manifest),
              'semantic_sha256': hashlib.sha256(Path(semantic.__file__).read_bytes()).hexdigest()}
    write_json(output, report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    main(parser.parse_args().output)
