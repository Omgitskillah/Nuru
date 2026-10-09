"""Build dedicated local indexes; never adopt or overwrite an existing database."""
import argparse
import json
from pathlib import Path
import time

from obsidian_mcp import semantic
from nomic_index_eval import MODEL, HOST, get, hashes


def main(root, output):
    revision = next(m for m in get('/api/tags')['models'] if m['name'] == MODEL)['digest']
    directory = root / '.validation-indexes'
    targets = {name: directory / (name + '.sqlite3') for name in ['VaultStewardValidation','DummyVaultValidation']}
    if any(path.exists() for path in targets.values()):
        raise ValueError('Refusing to overwrite an existing validation index')
    directory.mkdir(exist_ok=True)
    result = {'model':MODEL,'digest':revision,'indexes':[]}
    for name, path in targets.items():
        vault = root / name
        before = hashes(vault)
        env = {'OLLAMA_HOST':HOST,'OBSIDIAN_EMBED_MODEL':MODEL,'OBSIDIAN_EMBED_DIGEST':revision,
               'OBSIDIAN_CHUNK_CHARS':'400','OBSIDIAN_EMBED_BATCH_CHUNKS':'2',
               'OBSIDIAN_INDEX_EXCLUSIONS':'["Maintenance"]','OBSIDIAN_SEMANTIC_DB':str(path)}
        cfg = semantic.load_config(vault,env)
        calls = []
        live = semantic.ollama_embedder(cfg)
        def embed(texts):
            calls.append(len(texts))
            return live(texts)
        start = time.perf_counter()
        with semantic.connect(cfg) as conn:
            refreshed = semantic.refresh(conn,cfg,embed,force_hash=True)
            status = semantic.maintenance_status(conn,cfg)
            # Explicitly prove a reconciliation of this persistent index is inference-free.
            semantic.refresh(conn,cfg,lambda _: (_ for _ in ()).throw(AssertionError('No-change embedding')),force_hash=True)
        assert before == hashes(vault)
        result['indexes'].append({'vault':name,'environment':env,'embedded_notes':len(refreshed.embedded),
                                  'requests':len(calls),'wall_ms':(time.perf_counter()-start)*1000,
                                  'status':status,'source_unchanged':True,'no_change_zero_calls':True})
    output.write_text(json.dumps(result,indent=2)+'\n')
    (directory/'configuration.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({r['vault']:{'notes':r['embedded_notes'],'requests':r['requests']} for r in result['indexes']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path.cwd())
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    main(args.root.resolve(),args.output)
