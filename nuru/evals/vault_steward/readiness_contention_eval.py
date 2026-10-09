"""M5 frozen local retrieval contention workload; no chat or paid providers.

Five matched query lanes with Nomic v2 background indexing off/on. p95 gate <=10%.
This tests retrieval latency, not a complete Nuru answer or real-vault UX.
"""
from core.eval_artifacts import write_json
import argparse
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import platform
import resource
import statistics
import tempfile
import threading
import time

from obsidian_mcp import semantic
from core.vault_indexer import VaultIndexer
from nomic_index_eval import get, MODEL

PIN='ff9c2f10ef5e3722623a1b396e1e04efc27a93112c83e9b7b7b9ca1d05620965'
QUERIES=['How do I cook buckwheat pancakes?','How do I maintain a bicycle chain?',
         'Where is telescope alignment described?','How does microphone calibration work?']
NOTES=['Buckwheat pancakes use eggs and milk. Fry batter on a hot griddle.',
       'Bicycle chain maintenance uses lubricant. Check brakes and inflate tyres.',
       'Align the telescope with Polaris before observing distant galaxies.',
       'Microphone calibration uses an acoustic reference and measures noise.']


def percentile95(values):
    return sorted(values)[math.ceil(.95*len(values))-1]


def main(output,repeats):
    if next(m for m in get('/api/tags')['models'] if m['name']==MODEL)['digest'] != PIN:
        raise ValueError('Approved Nomic identity changed')
    report={'protocol':'M5-retrieval-contention-v1','model':MODEL,'digest':PIN,'ollama':get('/api/version'),
            'platform':platform.platform(),'semantic_sha256':hashlib.sha256(Path(semantic.__file__).read_bytes()).hexdigest(),
            'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'workload':{'query_count_per_mode':20,'backlog_notes':32,'chunk_chars':400,'batch_chunks':2,
                        'query_top_k':5,'gate_max_p95_increase':.10,'order':'off then on; warm cache and sequential-order bias disclosed'},
            'queries':QUERIES,'runs':[]}
    write_json(output, report)
    for repeat in range(repeats):
        with tempfile.TemporaryDirectory(prefix='nuru-contention-') as temporary:
            vault=Path(temporary).resolve()/'vault';vault.mkdir()
            for i,text in enumerate(NOTES):
                (vault/f'topic-{i}.md').write_text(text)
            # Backlog has several chunks per note, and never alters topic controls.
            for i in range(32):
                (vault/f'backlog-{i}.md').write_text((f'Archive item {i}: field station service log. Inspect connectors and record weather.\n'*16))
            config=semantic.load_config(vault,{'OBSIDIAN_EMBED_MODEL':MODEL,'OBSIDIAN_EMBED_DIGEST':PIN,
                    'OBSIDIAN_CHUNK_CHARS':'400','OBSIDIAN_EMBED_BATCH_CHUNKS':'2'})
            live=semantic.ollama_embedder(config)
            conn=semantic.connect(config);semantic.refresh(conn,config,live,force_hash=True);conn.close()
            source={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in vault.glob('*.md')}
            row={'repeat':repeat+1,'modes':{}};report['runs'].append(row)
            for mode in ('off','on'):
                active=threading.Event(); release=threading.Event();embedding_calls=[]
                def background_embed(texts):
                    active.set()
                    if not release.wait(5):
                        raise TimeoutError('query-start barrier timed out')
                    start=time.perf_counter();value=live(texts)
                    embedding_calls.append({'texts':len(texts),'ms':(time.perf_counter()-start)*1000})
                    return value
                worker=None
                if mode=='on':
                    for p in vault.glob('backlog-*.md'):
                        p.write_text(p.read_text()+'\nOne newly recorded service inspection.\n')
                    worker=VaultIndexer(config,background_embed,interval=30)
                    worker.start()
                    if not active.wait(5):
                        worker.stop();raise RuntimeError('Worker did not enter live embedding')
                usage_before=resource.getrusage(resource.RUSAGE_SELF);start=time.perf_counter()
                timings=[]; hits=[]; overlapped=0
                conn=semantic.connect(config)
                release.set()
                try:
                    for i in range(20):
                        if worker and worker.pending:
                            overlapped+=1
                        begin=time.perf_counter()
                        result=semantic.search(conn,config,live,QUERIES[i%4],5)
                        timings.append((time.perf_counter()-begin)*1000)
                        hits.append(any(r['path']==f'topic-{i%4}.md' for r in result))
                finally:
                    conn.close()
                    if worker:
                        worker.stop()
                seconds=time.perf_counter()-start;usage_after=resource.getrusage(resource.RUSAGE_SELF)
                sample={'latencies_ms':timings,'p50_ms':statistics.median(timings),'p95_ms':percentile95(timings),
                        'query_hits':sum(hits),'overlapped_queries':overlapped,'elapsed_seconds':seconds,
                        'process_cpu_seconds':usage_after.ru_utime+usage_after.ru_stime-usage_before.ru_utime-usage_before.ru_stime,
                        'process_peak_rss_bytes':usage_after.ru_maxrss if platform.system()=='Darwin' else usage_after.ru_maxrss*1024,
                        'background_embedding_requests':embedding_calls}
                if worker:
                    sample.update(worker_alive_after_stop=worker._thread.is_alive(),worker_error=worker.error,
                                  pending_status=worker.status)
                    # Reconcile unfinished work explicitly after cancellation.
                    resumed=VaultIndexer(config,live);resumed.run_once()
                    calls=[]
                    def counted(texts):
                        calls.append(texts);return live(texts)
                    idle=VaultIndexer(config,counted);idle.run_once()
                    sample['no_change_zero_calls']=not calls
                    sample['resumed_pending_count']=resumed.status['pending_count']
                row['modes'][mode]=sample
            row['p95_ratio']=row['modes']['on']['p95_ms']/row['modes']['off']['p95_ms']
            row['gate_pass']=row['p95_ratio']<=1.10 and row['modes']['on']['overlapped_queries']>0
            row['topic_bytes_unchanged']=all(source[f'topic-{i}.md']==hashlib.sha256((vault/f'topic-{i}.md').read_bytes()).hexdigest() for i in range(4))
            write_json(output, report);print(json.dumps({'repeat':repeat+1,'ratio':row['p95_ratio'],'gate':row['gate_pass']}),flush=True)
    ratios=[r['p95_ratio'] for r in report['runs']]
    report['summary']={'gate_pass':all(r['gate_pass'] for r in report['runs']),
        'median_p95_ratio':statistics.median(ratios),'min_p95_ratio':min(ratios),'max_p95_ratio':max(ratios),
        'source_topic_bytes_unchanged':all(r['topic_bytes_unchanged'] for r in report['runs']),
        'worker_stop_and_recovery_pass':all(not r['modes']['on']['worker_alive_after_stop'] and not r['modes']['on']['worker_error'] and r['modes']['on']['no_change_zero_calls'] and r['modes']['on']['resumed_pending_count']==0 for r in report['runs']),
        'scope':'Retrieval stage only; no full-turn latency or personal-vault acceptance claim.'}
    write_json(output, report);print(json.dumps(report['summary']),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--repeats',type=int,choices=range(1,6),default=5)
    args=p.parse_args();main(args.output,args.repeats)
