"""Create new local vaults without overwriting files or copying runtime indexes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from validation_fixture import FILES, ASSETS, CONFIG, ORACLE


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and not p.is_symlink()}


def main(source, target_root, output):
    source = source.resolve(strict=True)
    target_root = target_root.resolve(strict=True)
    fixture = target_root / 'VaultStewardValidation'
    copy = target_root / 'DummyVaultValidation'
    if fixture.exists() or copy.exists():
        raise ValueError('Refusing to overwrite an existing validation vault')
    original = hashes(source)
    fixture.mkdir()
    for name, content in {**FILES, **ASSETS}.items():
        path = fixture / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
    copy.mkdir()
    skipped = []
    for path in sorted(source.rglob('*')):
        rel = path.relative_to(source)
        if path.is_symlink() or any(part.startswith('.') for part in rel.parts) or any(part in {'logs', 'db', '__pycache__'} for part in rel.parts) or '.sqlite' in path.name or path.suffix == '.db':
            skipped.append(rel.as_posix())
            continue
        if path.is_file():
            dest = copy / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
    copied = hashes(copy)
    expected = {name: value for name, value in original.items() if name in copied}
    assert expected == copied and original == hashes(source)
    artifact = {'version': 1, 'source': str(source), 'source_unchanged': True,
                'copy': str(copy), 'copy_hashes': copied, 'skipped': skipped,
                'fixture': str(fixture), 'fixture_hashes': hashes(fixture), 'config': CONFIG, 'oracle': ORACLE,
                'grading_scope': 'supplemental first-use fixture; not independently human-labeled held-out acceptance'}
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'fixture_notes': len(FILES), 'fixture_assets': len(ASSETS), 'copied_files': len(copied), 'source_unchanged': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path('../DummyVault'))
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    main(args.source, args.root, args.output)
