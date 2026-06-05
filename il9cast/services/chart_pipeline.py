"""Auto-extracted from app.py — IL9Cast service module."""

@lru_cache(maxsize=100000)
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
        return None


def _perpendicular_distance(point, line_start, line_end):
    """Calculate perpendicular distance from a point to a line segment."""
    dx = line_end[0] - line_start[0]
    dy = line_end[1] - line_start[1]
    if dx == 0 and dy == 0:
        return math.sqrt((point[0] - line_start[0]) ** 2 + (point[1] - line_start[1]) ** 2)
    t = ((point[0] - line_start[0]) * dx + (point[1] - line_start[1]) * dy) / (dx * dx + dy * dy)
    t = max(0, min(1, t))
    proj_x = line_start[0] + t * dx
    proj_y = line_start[1] + t * dy
    return math.sqrt((point[0] - proj_x) ** 2 + (point[1] - proj_y) ** 2)


def rdp_simplify(points, epsilon):
    """
    Ramer-Douglas-Peucker polyline simplification.
    points: list of (x, y) tuples where x is normalized time (0-100), y is probability (0-100).
    Returns list of indices to keep.
    """
    if len(points) <= 2:
        return list(range(len(points)))

    # Find the point with the maximum distance from the line between first and last
    max_dist = 0
    max_idx = 0
    for i in range(1, len(points) - 1):
        d = _perpendicular_distance(points[i], points[0], points[-1])
        if d > max_dist:
            max_dist = d
            max_idx = i

    if max_dist > epsilon:
        # Recurse on both halves
        left = rdp_simplify(points[:max_idx + 1], epsilon)
        right = rdp_simplify(points[max_idx:], epsilon)
        # Combine, avoiding duplicate at split point
        right_shifted = [max_idx + idx for idx in right]
        return left[:-1] + right_shifted
    else:
        return [0, len(points) - 1]


# ===== CHART DATA CACHE =====
# Multi-slot cache: one entry per period:epsilon key.
# Invalidated only when file size changes (new snapshot appended).
# Pre-warmed after each data collection cycle so users always hit cache.
# Thundering-herd protection: per-key locks ensure only one thread computes
# a given cache entry while others wait for the result.
import threading as _threading
_chart_cache = {}  # key -> {'data': ..., 'time': ..., 'file_size': ..., 'etag': ...}
_chart_cache_lock = _threading.Lock()  # guards _chart_cache dict access
_chart_compute_locks = {}  # key -> Lock, prevents duplicate computation
_chart_compute_locks_lock = _threading.Lock()  # guards _chart_compute_locks dict

_jsonl_lines_cache = {'size': None, 'lines': None, 'ts_index': None}
_jsonl_lines_lock = _threading.Lock()


def _jsonl_data_size():
    """Byte size of the active snapshots file (gz preferred)."""
    gz_path = HISTORICAL_DATA_PATH + '.gz'
    try:
        if os.path.exists(gz_path):
            return os.path.getsize(gz_path)
        if os.path.exists(HISTORICAL_DATA_PATH):
            return os.path.getsize(HISTORICAL_DATA_PATH)
    except OSError:
        pass
    return 0


_TS_RE = re.compile(r'"timestamp":\s*"([^"]+)"')


def get_jsonl_raw_lines():
    """Return all JSONL lines, cached until the underlying file grows."""
    size = _jsonl_data_size()
    with _jsonl_lines_lock:
        if _jsonl_lines_cache['size'] == size and _jsonl_lines_cache['lines'] is not None:
            return _jsonl_lines_cache['lines']
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
        return _jsonl_lines_cache.get('ts_index') or []



def _chart_etag_key(file_size, period, epsilon):
    """Stable ETag for frozen archive data without serializing full payload."""
    return hashlib.md5(f"{file_size}:{period}:{epsilon:.2f}".encode()).hexdigest()[:16]


def _get_compute_lock(cache_key):
    """Get or create a per-key lock for thundering-herd protection."""
    with _chart_compute_locks_lock:
        if cache_key not in _chart_compute_locks:
            _chart_compute_locks[cache_key] = _threading.Lock()
        return _chart_compute_locks[cache_key]

