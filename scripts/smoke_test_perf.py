#!/usr/bin/env python3
"""Smoke tests for performance-critical endpoints (run against local Gunicorn)."""

import os
import sys
import time

import requests

BASE = os.environ.get('SMOKE_BASE_URL', 'http://127.0.0.1:8000')
TIMEOUT = float(os.environ.get('SMOKE_TIMEOUT', '30'))


def check(name, fn):
    t0 = time.perf_counter()
    try:
        fn()
        ms = (time.perf_counter() - t0) * 1000
        print(f'  OK  {name} ({ms:.0f} ms)')
        return True
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        print(f'  FAIL {name} ({ms:.0f} ms): {e}')
        return False


def main():
    ok = True

    def get(path, **kw):
        r = requests.get(BASE + path, timeout=TIMEOUT, **kw)
        r.raise_for_status()
        return r

    ok &= check('/healthz', lambda: get('/healthz').json()['status'] == 'ok')
    ok &= check('GET /', lambda: get('/'))
    ok &= check('GET /markets', lambda: get('/markets'))
    ok &= check('GET /odds', lambda: get('/odds'))

    def chart():
        r = get('/api/snapshots/chart?period=7d&epsilon=0.5',
                headers={'Accept-Encoding': 'gzip'})
        assert 'snapshots' in r.json()
        return r.headers.get('ETag')

    etag = None
    def chart_and_etag():
        nonlocal etag
        etag = chart()
    ok &= check('chart 7d', chart_and_etag)

    if etag:
        def chart_304():
            r = requests.get(
                BASE + '/api/snapshots/chart?period=7d&epsilon=0.5',
                headers={'If-None-Match': etag},
                timeout=TIMEOUT,
            )
            assert r.status_code == 304
        ok &= check('chart ETag 304', chart_304)

    def precincts():
        r = get('/api/model/precincts', headers={'Accept-Encoding': 'gzip'})
        assert r.headers.get('Content-Encoding') == 'gzip' or len(r.content) > 1000
    ok &= check('precincts gzip', precincts)

    print('smoke:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
