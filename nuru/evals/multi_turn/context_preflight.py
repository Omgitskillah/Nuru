"""Measure MT14's fixed request against declared ceilings without a model call.

--request is an actual captured {system, messages, tools} request after rendering
and tool discovery. Never substitute shortened or hand-written schemas.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from core.context_budget import available_tokens, input_budget


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text())
    if not isinstance(request.get('system'), str) or not request['system'] or not isinstance(request.get('messages'), list) or not request['messages'] or not isinstance(request.get('tools'), list) or not request['tools']:
        parser.error('provide the actual nonempty system, dated messages and complete tools')
    results = []
    for ceiling in (4096, 6144, 8192, 12288, 16384):
        remaining = available_tokens(ceiling, request['messages'], system=request['system'], tools=request['tools'])
        results.append({'ceiling': ceiling, 'reserve': ceiling - input_budget(ceiling),
                        'input_budget': input_budget(ceiling), 'fixed_request_estimate': input_budget(ceiling) - remaining,
                        'remaining': remaining, 'fits': remaining >= 0})
    print(json.dumps({'request_sha256': hashlib.sha256(args.request.read_bytes()).hexdigest(),
                      'estimates_not_tokenizer_guarantees': True, 'ceilings': results}, indent=2))


if __name__ == '__main__':
    main()
