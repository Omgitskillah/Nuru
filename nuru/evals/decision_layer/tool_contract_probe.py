"""Probe actual Rust MCP success/error flags without a model or vault writes.

Run after unit checks: python tool_contract_probe.py --out .runs/contracts.json
Python requires the configured Docker sandbox. No expert or Ollama calls occur.
"""
import argparse
import json
from pathlib import Path

from core import session, tool_dispatch
from core.providers.mcp_tools import MCPTools


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    python = session.python_server_params()
    if python is None:
        raise SystemExit('Python sandbox unavailable; contract coverage requires it.')
    tools = MCPTools([session.math_server_params(), python], frozenset({'calculate', 'statistics', 'run_python'}))
    tools.connect()
    records = []
    try:
        tools.list_tools()
        for name, arguments, success, code in [
            ('calculate', {'expression': '2+2'}, True, None),
            ('calculate', {'expression': 'not arithmetic'}, False, 'UNKNOWN_IDENTIFIER'),
            ('statistics', {'numbers': [1, 2, 3]}, True, None),
            ('statistics', {'numbers': []}, False, 'EMPTY_DATASET'),
            ('run_python', {'code': 'print(4)'}, True, None),
            ('run_python', {'code': "print('partial', flush=True)\nraise ValueError('probe error')"}, False, 'SCRIPT_ERROR'),
        ]:
            result = tools.call_tool_result(name, arguments)
            outcome = tool_dispatch.classify(name, result)
            records.append({'tool': name, 'arguments': arguments, 'protocol_error': result.is_error,
                'payload': result.text, 'success': outcome.success, 'kind': outcome.kind, 'code': outcome.code,
                'expected_success': success, 'passed': outcome.success == success and result.is_error == (not success) and outcome.code == code})
    finally:
        tools.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(records, indent=2))
    for row in records:
        print(f"{row['tool']}: protocol_error={row['protocol_error']}, success={row['success']}, code={row['code']}")
    if not all(r['passed'] for r in records):
        raise SystemExit('Contract probe failed; inspect saved records.')


if __name__ == '__main__':
    main()
