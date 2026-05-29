#!/usr/bin/env python3
"""Precompute chart API payloads for archive deploy (writes data/chart_cache/*.json)."""
import json
import os
import sys

os.environ.setdefault('IL9_SKIP_STARTUP_TASKS', '1')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'chart_cache')


def main():
    import app as il9

    os.makedirs(OUT, exist_ok=True)
    for period in ('all', '7d', '1d'):
        print(f'Computing {period}...')
        result = il9._compute_chart_data(period, 0.5, raw_lines=il9.get_jsonl_raw_lines())
        path = os.path.join(OUT, f'{period}.json')
        with open(path, 'w') as f:
            json.dump(result, f, separators=(',', ':'))
        print(f'  wrote {path} ({os.path.getsize(path)} bytes)')
    print('done')
    return 0


if __name__ == '__main__':
    sys.exit(main())
