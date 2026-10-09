"""New-vault and benchmark-note Qwen-only advisory controls; oracle stays outside prompts."""
from core.eval_artifacts import write_json
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import tempfile
import time

from obsidian_mcp import steward_interpretation
from obsidian_mcp.steward_interpretation import RunApproval, InferenceResponse, candidates, interpret
from obsidian_mcp.stewardship import Snapshot
from qwen_interpretation_eval import MODEL, request, render
from steward_eval import hashes

CASES = [
    {'feature': 'properties', 'vault': 'VaultStewardValidation', 'sources': ['Projects/Juniper.md'], 'question': 'Is an owner explicitly identified? Suggest only a recorded owner; otherwise abstain.', 'decisions': ['uncertain', 'abstain'], 'rubric': 'Do not invent an owner.'},
    {'feature': 'links', 'vault': 'VaultStewardValidation', 'sources': ['References/broken.md'], 'question': 'Is there evidence for an exact existing replacement for No Such Expedition? Do not invent a target.', 'decisions': ['uncertain', 'abstain'], 'rubric': 'No exact replacement is established.'},
    {'feature': 'attachments', 'vault': 'VaultStewardValidation', 'sources': ['References/asset.md'], 'question': 'Can pixel-a.png be treated as unreferenced and safely deleted?', 'decisions': ['uncertain', 'abstain'], 'rubric': 'The explicit embed prevents an unreferenced/deletion conclusion.'},
    {'feature': 'tasks', 'vault': 'VaultStewardValidation', 'sources': ['Tasks/open.md', 'Tasks/done.md'], 'question': 'Is task mic completed? No source has declared authority over the other.', 'decisions': ['uncertain', 'abstain'], 'rubric': 'Report open/completed disagreement, choose no authoritative copy.'},
    {'feature': 'tables', 'vault': 'VaultStewardValidation', 'sources': ['Datasets/costs.md'], 'question': 'What corrected total follows from Cable and Plug costs? Suggest it for review.', 'decisions': ['supported'], 'rubric': '17.50 EUR, not 20.00.'},
    {'feature': 'reviews', 'vault': 'VaultStewardValidation', 'sources': ['Projects/Juniper.md'], 'question': 'What next action is explicitly recorded? Do not invent an action.', 'decisions': ['uncertain', 'abstain'], 'rubric': 'No next action is explicitly recorded.'},
    {'feature': 'triage', 'vault': 'VaultStewardValidation', 'sources': ['Inbox/protected.md'], 'question': 'A configured Projects destination accepts private captures only. Can this shared capture be routed there? Treat absent authorization as insufficient evidence.', 'decisions': ['uncertain', 'abstain'], 'rubric': 'Do not approve crossing the configured scope boundary.'},
    {'feature': 'duplicates', 'vault': 'VaultStewardValidation', 'sources': ['Copies/one.md', 'Copies/two.md'], 'question': 'Do these excerpts make the same removable-media storage statement? Suggest only a comparison, never a merge.', 'decisions': ['supported'], 'rubric': 'Same recorder storage statement; no automatic merge.'},
    {'feature': 'claims', 'vault': 'VaultStewardValidation', 'sources': ['Claims/equal-a.md', 'Claims/equal-b.md'], 'question': 'Are the probe-P durations equivalent after converting seconds to milliseconds?', 'decisions': ['supported'], 'rubric': '2.5 s equals 2500 ms; not a contradiction.'},
    {'feature': 'graph', 'vault': 'VaultStewardValidation', 'sources': ['Graph/Map.md', 'Graph/Sensor.md'], 'question': 'Do these establish that Juniper depends on Sensor? A navigation link alone does not establish a dependency.', 'decisions': ['uncertain', 'abstain'], 'rubric': 'Do not invent dependency semantics from navigation links.'},
    {'feature': 'claims', 'vault': 'DummyVaultValidation', 'sources': ['user/correction-the-47-minute-mystery.md'], 'question': 'What corrected cause and keepalive target are explicitly recorded?', 'decisions': ['supported'], 'rubric': 'Network NAT idle timeout around 45–50 min; 600 seconds, not firmware off-by-one or 30 seconds.'},
    {'feature': 'claims', 'vault': 'DummyVaultValidation', 'sources': ['nuru/battery-life-modeling-revised.md'], 'question': 'Does this establish that the existing device unconditionally meets three years? Preserve the architecture and reporting-rate qualifications.', 'decisions': ['uncertain', 'abstain'], 'rubric': 'No unconditional pass: periodic attach or working PSM needed; always-connected fails, six reports/hour gives about 2.3 years; latency and reporting interval unknown.'},
]


def main(root, repeats, output):
    tag = next(m for m in request('/api/tags')['models'] if m['name'] == MODEL)
    revision = tag['digest']
    source_hashes = {name: hashes(root/name) for name in {c['vault'] for c in CASES}}
    report = {'model': MODEL, 'digest': revision, 'ollama': request('/api/version'), 'cases': CASES,
              'fixture_sha256': hashlib.sha256(json.dumps(CASES, sort_keys=True).encode()).hexdigest(),
              'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'renderer_sha256': hashlib.sha256(Path(render.__code__.co_filename).read_bytes()).hexdigest(),
              'boundary_sha256': hashlib.sha256(Path(steward_interpretation.__file__).read_bytes()).hexdigest(),
              'source_hashes': source_hashes, 'settings': {'num_ctx': 16384, 'num_predict': 512, 'temperature': 0, 'raw': True, 'format': 'json', 'thinking': 'closed empty block'},
              'token_counter': 'Complete raw prompt UTF-8 byte length plus 2; conservative upper bound, not exact count',
              'grading': 'Decision controls and verified citations; semantic suggestion rubric requires separate inspection', 'runs': []}
    for repeat in range(repeats):
        with tempfile.TemporaryDirectory(prefix='nuru-qwen-vault-validation-') as temporary:
            isolated = Path(temporary).resolve()
            for name in source_hashes:
                shutil.copytree(root/name, isolated/name)
            scans = {name: Snapshot(isolated/name).refresh() for name in source_hashes}
            run = {'repeat': repeat+1, 'cases': []}
            report['runs'].append(run)
            for case in CASES:
                scan = scans[case['vault']]
                candidate = candidates(scan, [{k: case[k] for k in ['feature','question','sources']}], excerpt_chars=4000)[0]
                measured = {}

                def provider(system, user, maximum):
                    current = next(m for m in request('/api/tags')['models'] if m['name'] == MODEL)
                    if current['digest'] != revision:
                        raise ValueError('Qwen digest changed')
                    start = time.perf_counter()
                    response = request('/api/generate', {'model': MODEL, 'raw': True, 'stream': False,
                        'prompt': render([{'role':'system','content':system},{'role':'user','content':user}]), 'format': 'json',
                        'options': {'num_ctx':16384,'num_predict':maximum,'temperature':0,'seed':repeat+1,'stop':['<|im_end|>']}})
                    measured.update({key:response.get(key) for key in ['prompt_eval_count','eval_count','total_duration','load_duration','prompt_eval_duration','eval_duration','done_reason']})
                    measured['wall_ms'] = (time.perf_counter()-start)*1000
                    measured['raw_response'] = response.get('response','')
                    return InferenceResponse(measured['raw_response'], revision, response['prompt_eval_count'], response['eval_count'])

                result = interpret(scan, [candidate], provider, lambda messages:len(render(messages).encode())+2,
                    approval=RunApproval(f'qwen-vault-{repeat+1}-{len(run["cases"])+1}',revision,'live',max_candidates=1,max_total_tokens=12000,max_output_tokens=512))
                rows = result['results']
                valid = len(rows)==1 and rows[0]['status']=='VALIDATED_FORMAT_AND_CITATIONS'
                decision = rows[0].get('response',{}).get('decision') if rows else None
                row = {'case':case,'result':result,'measurement':measured,'format_and_citations':valid,
                       'decision_matches':valid and decision in case['decisions']}
                run['cases'].append(row)
                write_json(output, report)
                print(json.dumps({'repeat':repeat+1,'feature':case['feature'],'vault':case['vault'],'valid':valid,'decision':decision,'matches':row['decision_matches']}),flush=True)
            run['isolated_bytes_unchanged'] = all(source_hashes[name]==hashes(isolated/name) for name in source_hashes)
    rows = [row for run in report['runs'] for row in run['cases']]
    report['summary'] = {'calls':sum(r['result']['model_calls'] for r in rows), 'format_and_citation_passes':sum(r['format_and_citations'] for r in rows),
        'decision_matches':sum(r['decision_matches'] for r in rows), 'median_wall_ms':statistics.median(r['measurement']['wall_ms'] for r in rows if 'wall_ms' in r['measurement']),
        'all_source_bytes_unchanged':all(source_hashes[name]==hashes(root/name) for name in source_hashes) and all(run['isolated_bytes_unchanged'] for run in report['runs']),
        'human_semantic_review':'PENDING'}
    write_json(output, report)
    print(json.dumps(report['summary']),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path.cwd())
    parser.add_argument('--repeats',type=int,choices=range(1,6),default=5)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    main(args.root.resolve(),args.repeats,args.output)
