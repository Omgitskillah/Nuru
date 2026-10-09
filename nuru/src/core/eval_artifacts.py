"""Portable, deterministic compression for archived evaluation evidence."""
import gzip
import json
from pathlib import Path


def read_json(path):
    path = Path(path)
    raw = path.read_bytes()
    if path.suffix == '.gz':
        raw = gzip.decompress(raw)
    return json.loads(raw)


def write_json(path, value):
    path = Path(path)
    raw = (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()
    path.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0) if path.suffix == '.gz' else raw)
