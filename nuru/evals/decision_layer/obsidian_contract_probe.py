"""Verify pre-mutation rejection receipts over MCP in a disposable vault, without a model."""
import argparse
import json
import tempfile
from pathlib import Path

from core import session, tool_dispatch
from core.providers.mcp_tools import MCPTools


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    records = []
    with tempfile.TemporaryDirectory(prefix='nuru-write-contract-') as scratch:
        params = session.OBSIDIAN_SERVER_PARAMS.model_copy(update={'env': {
            **session.OBSIDIAN_SERVER_PARAMS.env, 'VAULT_PATH': scratch, 'OBSIDIAN_READ_ONLY': '0',
            'OBSIDIAN_SEMANTIC_DB': str(Path(scratch) / 'semantic.sqlite3'),
            'OBSIDIAN_MCP_LOG_DIR': str(Path(scratch) / 'logs')}})
        tools = MCPTools([params], frozenset({'write_note', 'rename_or_move_file'}))
        tools.connect()
        try:
            tools.list_tools()
            for name, arguments, certainty in [
                ('write_note', {'content': 'missing note'}, 'not_completed'),
                ('write_note', {'note': 'Contract probe', 'content': 'original'}, 'completed'),
                ('write_note', {'note': 'nuru/contract-probe.md', 'content': 'replacement', 'mode': 'replace'}, 'not_completed'),
                ('write_note', {'note': 'nuru/contract-probe.md', 'content': 'appended'}, 'completed'),
                ('rename_or_move_file', {'source_path': 'nuru/contract-probe.md', 'destination_path': 'nuru/contract-probe.md'}, 'not_completed'),
                ('rename_or_move_file', {'source_path': 'nuru/contract-probe.md', 'destination_path': 'nuru/moved.md'}, 'completed'),
            ]:
                result = tools.call_tool_result(name, arguments)
                outcome = tool_dispatch.classify(name, result)
                records.append({'tool': name, 'arguments': arguments, 'payload': result.text,
                    'protocol_error': result.is_error, 'certainty': outcome.certainty, 'repairable': outcome.repairable,
                    'passed': outcome.certainty == certainty and outcome.success == (certainty == 'completed')
                              and (certainty != 'not_completed' or outcome.repairable)})
            final = (Path(scratch) / 'nuru/moved.md').read_text()
            assert final.endswith('appended') and 'replacement' not in final
            assert not (Path(scratch) / 'trash').exists()
        finally:
            tools.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(records, indent=2))
    for row in records:
        print(f"{row['tool']}: {row['certainty']}, repairable={row['repairable']}, passed={row['passed']}")
    if not all(r['passed'] for r in records):
        raise SystemExit('Write contract probe failed; inspect saved records.')


if __name__ == '__main__':
    main()
