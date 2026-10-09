"""Replay all saved M4 responses without inference against the final boundary.

Verify unchanged prompt contracts after stricter stale-asset/coverage guards;
replay is regression evidence, not new model-quality sampling.
"""
from core.eval_artifacts import read_json, write_json
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

import readiness_advisory_eval as e
from obsidian_mcp.stewardship import Snapshot
from obsidian_mcp.link_integrity import Policy
from obsidian_mcp.steward_interpretation import RunApproval, InferenceResponse, interpret
from steward_eval import hashes


def main(source,output):
    saved=read_json(source);results=[]
    with tempfile.TemporaryDirectory(prefix='nuru-readiness-replay-') as temporary:
        root=Path(temporary).resolve()
        for lane,files in [('development',e.FILES),('fresh-v1',e.FRESH_FILES)]:
            vault=root/lane
            for path,text in files.items():
                target=vault/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
            for path,data in (e.ASSETS if lane=='development' else {'image.png':b'fixture image'}).items():
                target=vault/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        before=hashes(root)
        for old in saved['runs']:
            case=old['case'];scan=Snapshot(root/case['lane'],policy=Policy(exclusions=('Templates','Maintenance'))).refresh()
            config={**e.CONFIG,'tables':[{**c,'glob':'*.md'} for c in e.CONFIG['tables']]}
            candidate=e.candidates(scan,[{k:case[k] for k in ('feature','sources','question')}],config=config)[0]
            measured=old['measurement']
            def provider(*_):
                return InferenceResponse(measured['response'],e.PIN,measured['prompt_eval_count'],measured['eval_count'])
            current=interpret(scan,[candidate],provider,lambda m:len(e.render(m).encode())+2,
                approval=RunApproval('cached-replay',e.PIN,'fake',max_candidates=1,max_total_tokens=16000,max_output_tokens=1024))
            status=current['results'][0]['status']
            results.append({'repeat':old['repeat'],'case_index':old['case_index'],
                            'candidate_identical':candidate==old['candidate'],
                            'status_identical':status==old['result']['results'][0]['status'],
                            'status':status,'model_calls':current['model_calls']})
        artifact={'saved_results_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                  'current_boundary_sha256':hashlib.sha256(Path(e.steward_interpretation.__file__).read_bytes()).hexdigest(),
                  'current_evidence_sha256':hashlib.sha256(Path(e.steward_evidence.__file__).read_bytes()).hexdigest(),
                  'results':results,'source_bytes_unchanged':before==hashes(root),'model_calls':0,
                  'pass':all(r['candidate_identical'] and r['status_identical'] for r in results) and before==hashes(root)}
    write_json(output, artifact);print(json.dumps({k:v for k,v in artifact.items() if k!='results'}))
    raise SystemExit(0 if artifact['pass'] else 1)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();main(args.source,args.output)
