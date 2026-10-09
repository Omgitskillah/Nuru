"""Reconstruct tracked synthetic inputs; smoke real CLI/MCP without inference."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from validation_fixture import FILES, ASSETS, CONFIG, ORACLE
from steward_eval import hashes, grade


def main(output):
    with tempfile.TemporaryDirectory(prefix='nuru-release-local-check-') as temporary:
        root=Path(temporary).resolve();vault=root/'vault';vault.mkdir()
        for name,data in {**FILES,**ASSETS}.items():
            path=vault/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(data.encode() if isinstance(data,str) else data)
        config=root/'config.json';config.write_text(json.dumps(CONFIG))
        before=hashes(vault)
        env={**os.environ,'VAULT_PATH':str(vault),'OBSIDIAN_STEWARD_CONFIG':str(config),
             'OBSIDIAN_SEMANTIC_DB':'','NURU_STEWARD_ENABLED':'0','OBSIDIAN_READ_ONLY':'1'}
        reports={}
        for module in ('obsidian_mcp.steward','core.vault_steward'):
            proc=subprocess.run([sys.executable,'-m',module,str(vault),'--config',str(config)],env=env,capture_output=True,text=True,timeout=30,check=True)
            report=json.loads(proc.stdout)
            # CLI default pagination includes all 16 expected occurrences.
            reports[module]={'grade':grade(ORACLE['findings'],report['findings']),
                             'complete':report['complete'],'model_calls':report['model_calls']}
        os.environ.update({k:env[k] for k in ('VAULT_PATH','OBSIDIAN_STEWARD_CONFIG','OBSIDIAN_SEMANTIC_DB','NURU_STEWARD_ENABLED','OBSIDIAN_READ_ONLY')})
        from obsidian_mcp import server
        server.VAULT_PATH=vault
        tools=asyncio.run(server.mcp.list_tools())
        result=asyncio.run(server.mcp.call_tool('audit_stewardship',{}))
        artifact={'cli':reports,'mcp_tool_registered':any(t.name=='audit_stewardship' for t in tools),
                  'mcp_call_success':result.is_error is not True,
                  'source_bytes_unchanged':before==hashes(vault),
                  'fixture_hash':hashlib.sha256(json.dumps([FILES,CONFIG,ORACLE],sort_keys=True).encode()).hexdigest(),
                  'external_vault_required':False,'model_calls':0}
        artifact['pass']=all(r['grade']['exact'] and r['complete'] and r['model_calls']==0 for r in reports.values()) and artifact['mcp_tool_registered'] and artifact['mcp_call_success'] and artifact['source_bytes_unchanged']
        output.write_text(json.dumps(artifact,indent=2)+'\n');print(json.dumps(artifact))
        raise SystemExit(0 if artifact['pass'] else 1)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();main(args.output)
