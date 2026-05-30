#!/usr/bin/env python3
"""Chart pipeline performance patches for archive mode."""
from pathlib import Path
import re

APP = Path(__file__).resolve().parents[1] / "app.py"
t = APP.read_text()

# Fast timestamp parser with lru_cache
if "@lru_cache" not in t.split("def parse_snapshot_timestamp")[0][-500:]:
    t = t.replace(
        "import math\n",
        "import math\nfrom functools import lru_cache\nimport bisect\n",
        1,
    )
    old_parser = '''def parse_snapshot_timestamp(ts_str):
    """
    Parse ISO timestamp string to UTC datetime.
    Handles both Z-suffix and no-suffix (all are UTC).
    Returns None if unparseable.
    """
    if not ts_str:
        return None
    ts_clean = ts_str.rstrip('Z')
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
        try:
            dt = datetime.strptime(ts_clean, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None'''
    new_parser = '''@lru_cache(maxsize=100000)
def parse_snapshot_timestamp(ts_str):
    """Parse ISO timestamp to UTC datetime. Cached for chart hot path."""
    if not ts_str:
        return None
    try:
        s = ts_str.replace('Z', '+00:00') if ts_str.endswith('Z') else ts_str
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None'''
    if old_parser in t:
        t = t.replace(old_parser, new_parser, 1)

# Extend JSONL cache with timestamp index
if "'ts_index'" not in t:
    t = t.replace(
        "_jsonl_lines_cache = {'size': None, 'lines': None}",
        "_jsonl_lines_cache = {'size': None, 'lines': None, 'ts_index': None}",
        1,
    )
    old_get = '''def get_jsonl_raw_lines():
    """Return all JSONL lines, cached until the underlying file grows."""
    size = _jsonl_data_size()
    with _jsonl_lines_lock:
        if _jsonl_lines_cache['size'] == size and _jsonl_lines_cache['lines'] is not None:
            return _jsonl_lines_cache['lines']
    try:
        f = _open_jsonl(HISTORICAL_DATA_PATH)
        if f is None:
            return []
        with f:
            lines = [line.strip() for line in f if line.strip()]
    except (IOError, OSError):
        return []
    with _jsonl_lines_lock:
        _jsonl_lines_cache['size'] = size
        _jsonl_lines_cache['lines'] = lines
    return lines'''
    new_get = '''_TS_RE = re.compile(r'"timestamp":\\s*"([^"]+)"')


def get_jsonl_raw_lines():
    """Return all JSONL lines, cached until the underlying file grows."""
    size = _jsonl_data_size()
    with _jsonl_lines_lock:
        if _jsonl_lines_cache['size'] == size and _jsonl_lines_cache['lines'] is not None:
            return _jsonl_lines_cache['lines']
    import re as _re_mod
    lines = []
    ts_index = []
    try:
        f = _open_jsonl(HISTORICAL_DATA_PATH)
        if f is None:
            return []
        with f:
            for i, line in enumerate(f):
                s = line.strip()
                if not s:
                    continue
                lines.append(s)
                m = _TS_RE.search(s)
                if m:
                    dt = parse_snapshot_timestamp(m.group(1))
                    if dt:
                        ts_index.append((dt.timestamp(), i))
    except (IOError, OSError):
        return []
    with _jsonl_lines_lock:
        _jsonl_lines_cache['size'] = size
        _jsonl_lines_cache['lines'] = lines
        _jsonl_lines_cache['ts_index'] = ts_index
    return lines


def get_jsonl_ts_index():
    """Epoch timestamp + line index pairs for fast period windows."""
    get_jsonl_raw_lines()
    with _jsonl_lines_lock:
        return _jsonl_lines_cache.get('ts_index') or []'''
    if old_get in t:
        t = t.replace(old_get, new_get, 1)

# Archive chart cache max-age + cheap etag helper
if "def _chart_etag_key" not in t:
    insert = '''
def _chart_etag_key(file_size, period, epsilon):
    """Stable ETag for frozen archive data without serializing full payload."""
    return hashlib.md5(f"{file_size}:{period}:{epsilon:.2f}".encode()).hexdigest()[:16]


'''
    t = t.replace("def _get_compute_lock(cache_key):", insert + "def _get_compute_lock(cache_key):", 1)

    t = t.replace(
        "etag = hashlib.md5(json.dumps(result, separators=(',', ':')).encode()).hexdigest()[:16]",
        "etag = _chart_etag_key(current_file_size, period, 0.5)",
        2,
    )

    t = t.replace(
        "resp.headers['Cache-Control'] = 'public, max-age=120'",
        "resp.headers['Cache-Control'] = 'public, max-age=86400'",
    )

# Bisect for 7d/1d window - patch the backward scan block
old_window = '''        start_idx = len(source_lines)
        for i in range(len(source_lines) - 1, -1, -1):
            m = _ts_re.search(source_lines[i])
            if not m:
                continue
            dt = parse_snapshot_timestamp(m.group(1))
            if dt is None:
                continue
            if dt >= cutoff:
                start_idx = i
            else:
                break
        lines_to_parse = source_lines[start_idx:]'''
new_window = '''        ts_index = get_jsonl_ts_index()
        start_idx = 0
        if ts_index and cutoff is not None:
            cutoff_epoch = cutoff.timestamp()
            start_idx = bisect.bisect_left(ts_index, (cutoff_epoch, 0))
            if start_idx < len(ts_index):
                start_idx = ts_index[start_idx][1]
            else:
                start_idx = len(source_lines)
        else:
            start_idx = len(source_lines)
            for i in range(len(source_lines) - 1, -1, -1):
                m = _ts_re.search(source_lines[i])
                if not m:
                    continue
                dt = parse_snapshot_timestamp(m.group(1))
                if dt is None:
                    continue
                if dt >= cutoff:
                    start_idx = i
                else:
                    break
        lines_to_parse = source_lines[start_idx:]'''
if old_window in t and "get_jsonl_ts_index()" not in t.split("lines_to_parse = source_lines[start_idx:]")[0][-400:]:
    t = t.replace(old_window, new_window, 1)

# Load static chart cache at end of _prewarm if files exist
if "_load_disk_chart_cache" not in t:
    disk_fn = '''
def _load_disk_chart_cache():
    """Load precomputed chart JSON from data/chart_cache/ if present (archive deploy)."""
    cache_dir = os.path.join(os.path.dirname(__file__), 'data', 'chart_cache')
    if not os.path.isdir(cache_dir):
        return False
    size = _jsonl_data_size()
    now = _time.time()
    loaded = 0
    for period in ('all', '7d', '1d'):
        path = os.path.join(cache_dir, f'{period}.json')
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r') as f:
                result = json.load(f)
            key = f'{period}:0.5'
            etag = _chart_etag_key(size, period, 0.5)
            with _chart_cache_lock:
                _chart_cache[key] = {
                    'data': result,
                    'time': now,
                    'file_size': size,
                    'etag': etag,
                }
            loaded += 1
        except (IOError, OSError, json.JSONDecodeError):
            continue
    return loaded == 3


'''
    t = t.replace("def _prewarm_chart_cache():", disk_fn + "def _prewarm_chart_cache():", 1)
    t = t.replace(
        "    global _chart_cache\n    # Determine file size",
        "    global _chart_cache\n    if _load_disk_chart_cache():\n        return\n    # Determine file size",
        1,
    )

# gunicorn default workers=2 for memory
gunicorn = Path(__file__).resolve().parents[1] / "gunicorn.conf.py"
gt = gunicorn.read_text()
if 'min(4,' in gt:
    gt = gt.replace(
        'workers = int(os.environ.get("WEB_CONCURRENCY", min(4, multiprocessing.cpu_count() + 1)))',
        'workers = int(os.environ.get("WEB_CONCURRENCY", min(2, multiprocessing.cpu_count() + 1)))',
        1,
    )
    gunicorn.write_text(gt)

APP.write_text(t)
print("app.py + gunicorn patched")
