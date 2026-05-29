from flask import Flask, jsonify, render_template, request, send_file
from functools import wraps
import random
import math
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
import requests
import json
import os
import time as _time
import shutil
import gzip

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 86400  # Cache static files for 1 day (safe due to ?v= cache-buster)

from performance import init_performance
init_performance(app)


@app.url_defaults
def _append_static_version(endpoint, values):
    """Append a ?v=<mtime> cache-buster to every url_for('static', ...) call
    so browsers automatically pick up changed CSS/JS without a hard refresh."""
    if endpoint != 'static':
        return
    filename = values.get('filename')
    if not filename or 'v' in values:
        return
    try:
        filepath = os.path.join(app.static_folder, filename)
        values['v'] = int(os.path.getmtime(filepath))
    except OSError:
        pass


@app.after_request
def _no_cache_html(response):
    """Prevent browsers from serving stale HTML pages. Static assets and JSON
    APIs keep their own cache headers; only text/html gets forced revalidation."""
    if response.mimetype == 'text/html' and 'Cache-Control' not in response.headers:
        response.headers['Cache-Control'] = 'no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


# ===== PATH RESOLUTION =====

def resolve_data_path(filename='historical_snapshots.jsonl'):
    """
    Resolve the correct data directory, checking for actual data files.
    Checks for both plain and .gz versions of the file.
    Priority: DATA_DIR env var -> /app/data/ -> /data/ -> local data/
    """
    configured_dir = os.environ.get('DATA_DIR', '').strip()
    if configured_dir:
        return os.path.join(configured_dir, filename)

    # Check all candidate dirs for actual data (plain or gzipped)
    local_data = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
    for candidate_dir in ['/app/data', '/data', local_data]:
        candidate_path = os.path.join(candidate_dir, filename)
        gz_path = candidate_path + '.gz'
        if os.path.exists(gz_path) or os.path.exists(candidate_path):
            return candidate_path

    # Fallback to local data/ directory
    return os.path.join(local_data, filename)


# Path to historical data storage (JSONL format - JSON Lines)
HISTORICAL_DATA_PATH = resolve_data_path('historical_snapshots.jsonl')

# Seed data path - git-tracked backup that Railway will use to initialize the volume
SEED_DATA_PATH = os.path.join(os.path.dirname(__file__), 'data', 'seed_snapshots.json')

# Legacy JSON path for migration
LEGACY_JSON_PATH = os.path.join(os.path.dirname(__file__), 'data', 'historical_snapshots.json')
REPO_CSV_PATH = os.path.join(os.path.dirname(__file__), 'il9cast_historical_data.csv')

# ===== EMAIL ALERT CONFIGURATION =====
SUBSCRIBERS_PATH = resolve_data_path('email_subscribers.jsonl')
RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
RESEND_FROM_EMAIL = os.environ.get('RESEND_FROM_EMAIL', 'alerts@il9.org')
RESEND_FROM = f"IL9Cast <{RESEND_FROM_EMAIL}>"  # Display name + email
EMAIL_SECRET_SALT = os.environ.get('EMAIL_SECRET_SALT')
if not EMAIL_SECRET_SALT:
    import warnings
    warnings.warn('EMAIL_SECRET_SALT is not set! Email tokens will be insecure.', stacklevel=1)
    EMAIL_SECRET_SALT = 'il9cast-change-me'

# Admin API authentication token (must be set in production)
ADMIN_API_TOKEN = os.environ.get('ADMIN_API_TOKEN')
SITE_BASE_URL = os.environ.get('SITE_BASE_URL', 'https://il9.org/')
SWING_THRESHOLD = 5.0  # percentage points to trigger alert
_swing_debounce = {}  # candidate_name -> last_alert_time (UTC timestamp)
_daily_summary_sent = None  # date string of last sent daily summary

# ===== JSONL HELPER FUNCTIONS =====

def _acquire_file_lock(lock_path):
    """Acquire an exclusive inter-process file lock and return the lock file handle."""
    import fcntl
    lock_file = open(lock_path, 'a+')
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    return lock_file


def _release_file_lock(lock_file):
    """Release an inter-process file lock."""
    import fcntl
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def backup_file(filepath, reason='manual'):
    """Create a timestamped backup copy of filepath if it exists."""
    if not os.path.exists(filepath):
        return None
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup_path = f"{filepath}.backup.{reason}.{ts}"
    shutil.copy2(filepath, backup_path)
    print(f"[{datetime.now().isoformat()}] Backup created: {backup_path}")
    return backup_path




def _parse_bool(value):
    """Parse common truthy/falsy values to bool."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {'1', 'true', 'yes', 'y'}


def _safe_float(value, default=0.0):
    """Best-effort numeric coercion for probabilities coming from mixed data sources."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_snapshots_from_csv(csv_path):
    """Load wide historical CSV (timestamp,candidate,probability,hasKalshi[,interpolated]) into snapshot JSON objects."""
    import csv

    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    grouped = {}
    interpolated_flags = {}
    with open(csv_path, 'r', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = (row.get('timestamp') or '').strip()
            name = (row.get('candidate') or '').strip()
            if not ts or not name:
                continue
            try:
                prob = float(row.get('probability', 0) or 0)
            except (TypeError, ValueError):
                prob = 0.0
            grouped.setdefault(ts, []).append({
                'name': name,
                'probability': prob,
                'hasKalshi': _parse_bool(row.get('hasKalshi'))
            })
            if _parse_bool(row.get('interpolated')):
                interpolated_flags[ts] = True

    snapshots = []
    for ts, candidates in grouped.items():
        snap = {'timestamp': ts, 'candidates': candidates}
        if interpolated_flags.get(ts):
            snap['interpolated'] = True
        snapshots.append(snap)

    snapshots.sort(key=lambda s: parse_snapshot_timestamp(s.get('timestamp')) or datetime.min.replace(tzinfo=timezone.utc))
    return snapshots


def _interpolate_snapshots(start_snapshot, end_snapshot, step_count, add_noise=False):
    """Linearly interpolate snapshots between two timestamped snapshots (exclusive endpoints).

    If add_noise=True, adds small random-walk fluctuations around the trend line
    to make interpolated data look like natural market movement. Each snapshot is
    also flagged with 'interpolated': True.
    """
    if step_count <= 0:
        return []

    start_dt = parse_snapshot_timestamp(start_snapshot.get('timestamp'))
    end_dt = parse_snapshot_timestamp(end_snapshot.get('timestamp'))
    if not start_dt or not end_dt or end_dt <= start_dt:
        return []

    start_map = {c.get('name'): c for c in start_snapshot.get('candidates', [])}
    end_map = {c.get('name'): c for c in end_snapshot.get('candidates', [])}
    names = sorted(set(start_map.keys()) | set(end_map.keys()))

    # For noise: track per-candidate random walk offset
    noise_state = {name: 0.0 for name in names}
    # Use a seeded RNG so interpolated data is deterministic for same inputs
    rng = random.Random(hash(str(start_snapshot.get('timestamp', '')) + str(end_snapshot.get('timestamp', ''))))

    out = []
    total_seconds = (end_dt - start_dt).total_seconds()
    for step in range(1, step_count + 1):
        ratio = step / (step_count + 1)
        ts = start_dt + timedelta(seconds=total_seconds * ratio)
        candidates = []
        for name in names:
            start_prob = float(start_map.get(name, {}).get('probability', 0) or 0)
            end_prob = float(end_map.get(name, {}).get('probability', 0) or 0)
            interp_prob = start_prob + ((end_prob - start_prob) * ratio)

            if add_noise and step_count > 2:
                # Random walk with mean-reversion toward the trend line
                # Noise magnitude scales with candidate's probability level
                magnitude = max(0.05, min(0.4, interp_prob * 0.006))
                noise_state[name] += rng.gauss(0, magnitude)
                # Mean-revert: pull noise back toward zero
                noise_state[name] *= 0.92
                # Dampen noise near endpoints so it connects smoothly
                edge_dampen = min(ratio, 1.0 - ratio) * 4.0
                edge_dampen = min(1.0, edge_dampen)
                interp_prob += noise_state[name] * edge_dampen
                # Clamp to valid range
                interp_prob = max(0.0, min(100.0, interp_prob))

            has_kalshi = bool(start_map.get(name, {}).get('hasKalshi', False) or end_map.get(name, {}).get('hasKalshi', False))
            candidates.append({
                'name': name,
                'probability': round(interp_prob, 1),
                'hasKalshi': has_kalshi
            })
        snap = {
            'timestamp': ts.isoformat().replace('+00:00', 'Z'),
            'candidates': candidates
        }
        if add_noise:
            snap['interpolated'] = True
        out.append(snap)
    return out


def bridge_to_present(filepath, interval_minutes=3, max_bridge_hours=72):
    """Append flat-interpolated snapshots from the last snapshot in the file up to now.

    Uses the last snapshot's values as both start and end so the bridge is a flat line.
    Returns a stats dict.
    """
    snapshots = read_snapshots_jsonl(filepath)
    if not snapshots:
        return {'bridged': False, 'reason': 'no_data'}

    last = snapshots[-1]
    last_dt = parse_snapshot_timestamp(last.get('timestamp'))
    now = datetime.now(timezone.utc)

    if not last_dt:
        return {'bridged': False, 'reason': 'no_timestamp'}

    gap_seconds = (now - last_dt).total_seconds()
    gap_hours = gap_seconds / 3600.0

    if gap_seconds <= interval_minutes * 60:
        return {'bridged': False, 'reason': 'no_gap', 'gap_hours': round(gap_hours, 2)}

    if gap_hours > max_bridge_hours:
        return {'bridged': False, 'reason': 'gap_too_large', 'gap_hours': round(gap_hours, 2)}

    # Create a "now" endpoint with same values (flat bridge)
    end_snapshot = {
        'candidates': last.get('candidates', []),
        'timestamp': now.isoformat().replace('+00:00', 'Z')
    }

    step_count = int(gap_seconds // (interval_minutes * 60)) - 1
    step_count = max(0, min(step_count, 5000))

    bridge = _interpolate_snapshots(last, end_snapshot, step_count, add_noise=True)

    if not bridge:
        return {'bridged': False, 'reason': 'no_bridge_steps'}

    # Bulk append bridge snapshots with a single lock acquire
    lock_path = filepath + '.lock'
    lock_file = _acquire_file_lock(lock_path)
    try:
        with open(filepath, 'a') as f:
            for snap in bridge:
                f.write(json.dumps(snap, separators=(',', ':')) + '\n')
            f.flush()
            os.fsync(f.fileno())
    finally:
        _release_file_lock(lock_file)

    return {
        'bridged': True,
        'snapshots_added': len(bridge),
        'gap_hours': round(gap_hours, 2),
        'from': last.get('timestamp'),
        'to': end_snapshot['timestamp']
    }


def recover_snapshots_from_csv_and_current(csv_path, current_path, output_path, bridge_interval_minutes=3, max_bridge_hours=72, dry_run=True, csv_only=False):
    """Rebuild timeline by stitching CSV history with current JSONL snapshots and optional interpolation bridge."""
    csv_snapshots = load_snapshots_from_csv(csv_path)
    current_snapshots = read_snapshots_jsonl(current_path)

    if not csv_snapshots:
        raise ValueError('No snapshots found in CSV source')
    if not csv_only and not current_snapshots:
        raise ValueError('No snapshots found in current JSONL source')

    current_snapshots = [s for s in current_snapshots if parse_snapshot_timestamp(s.get('timestamp'))]
    csv_snapshots = [s for s in csv_snapshots if parse_snapshot_timestamp(s.get('timestamp'))]

    current_snapshots.sort(key=lambda s: parse_snapshot_timestamp(s.get('timestamp')))
    csv_snapshots.sort(key=lambda s: parse_snapshot_timestamp(s.get('timestamp')))

    if csv_only:
        merged = []
        seen = set()
        for snap in csv_snapshots:
            ts = snap.get('timestamp')
            if not ts or ts in seen:
                continue
            seen.add(ts)
            merged.append(snap)
    else:
        # CSV is authoritative for its time range. Within [csv_min, csv_max],
        # use ONLY CSV data. Outside that range, keep current JSONL data.
        # This cleanly replaces any bridge/interpolated/stale data without
        # relying on an 'interpolated' flag that may have been lost.
        csv_dts = [parse_snapshot_timestamp(s.get('timestamp')) for s in csv_snapshots
                   if parse_snapshot_timestamp(s.get('timestamp'))]
        csv_min_dt = min(csv_dts) if csv_dts else None
        csv_max_dt = max(csv_dts) if csv_dts else None

        by_ts = {}
        # First: add all CSV data (authoritative for its range)
        for snap in csv_snapshots:
            ts = snap.get('timestamp')
            if ts:
                by_ts[ts] = snap
        # Second: add current JSONL data ONLY for timestamps outside CSV range
        for snap in current_snapshots:
            ts = snap.get('timestamp')
            if not ts:
                continue
            snap_dt = parse_snapshot_timestamp(ts)
            if not snap_dt:
                continue
            if csv_min_dt and csv_max_dt and csv_min_dt <= snap_dt <= csv_max_dt:
                # Within CSV range: only keep if CSV already has this exact timestamp
                # (CSV version is already in by_ts, don't overwrite it)
                if ts not in by_ts:
                    continue  # drop non-CSV data within CSV range
            else:
                # Outside CSV range: keep current data
                by_ts[ts] = snap
        merged = sorted(by_ts.values(), key=lambda s: parse_snapshot_timestamp(s.get('timestamp')))

        # Bridge the gap between CSV end and first post-CSV current data
        if merged and csv_max_dt:
            # Find first snapshot after CSV range
            first_post_csv = None
            for snap in merged:
                snap_dt = parse_snapshot_timestamp(snap.get('timestamp'))
                if snap_dt and snap_dt > csv_max_dt:
                    first_post_csv = snap
                    first_post_csv_dt = snap_dt
                    break
            # Find last CSV snapshot
            last_csv_snap = None
            for snap in reversed(merged):
                snap_dt = parse_snapshot_timestamp(snap.get('timestamp'))
                if snap_dt and snap_dt <= csv_max_dt:
                    last_csv_snap = snap
                    break

            if last_csv_snap and first_post_csv:
                last_csv_dt = parse_snapshot_timestamp(last_csv_snap.get('timestamp'))
                gap_hours = (first_post_csv_dt - last_csv_dt).total_seconds() / 3600.0
                if gap_hours > 0 and gap_hours <= max_bridge_hours:
                    step_count = int((first_post_csv_dt - last_csv_dt).total_seconds() // (bridge_interval_minutes * 60)) - 1
                    step_count = max(0, min(step_count, 5000))
                    bridge = _interpolate_snapshots(last_csv_snap, first_post_csv, step_count, add_noise=True)
                    for snap in bridge:
                        ts = snap.get('timestamp')
                        if ts and ts not in by_ts:
                            by_ts[ts] = snap
                    merged = sorted(by_ts.values(), key=lambda s: parse_snapshot_timestamp(s.get('timestamp')))

    stats = {
        'csv_snapshots': len(csv_snapshots),
        'current_snapshots': len(current_snapshots),
        'merged_total': len(merged),
        'first_timestamp': merged[0]['timestamp'] if merged else None,
        'last_timestamp': merged[-1]['timestamp'] if merged else None,
        'dry_run': dry_run
    }

    if dry_run:
        return stats

    lock_path = output_path + '.lock'
    lock_file = _acquire_file_lock(lock_path)
    temp_path = output_path + '.recover_tmp'
    try:
        backup_path = backup_file(output_path, reason='recovery')
        with open(temp_path, 'w') as f:
            for snap in merged:
                f.write(json.dumps(snap, separators=(',', ':')) + '\n')
        os.replace(temp_path, output_path)
        stats['backup_path'] = backup_path
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        _release_file_lock(lock_file)

    return stats


def read_snapshots_jsonl(filepath):
    """
    Read snapshots from JSONL file (plain or gzipped).
    Each line is a separate JSON object.
    Returns list of snapshot dictionaries.
    """
    snapshots = []
    # Try gzipped version first, then plain
    gz_path = filepath + '.gz'
    if os.path.exists(gz_path):
        actual_path = gz_path
        opener = lambda p: gzip.open(p, 'rt', encoding='utf-8')
    elif os.path.exists(filepath):
        actual_path = filepath
        opener = lambda p: open(p, 'r')
    else:
        return snapshots

    try:
        with opener(actual_path) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                if '\x00' in line:
                    preview = line[:120]
                    print(
                        f"[{datetime.now().isoformat()}] Corrupt NUL bytes at line {line_num}. "
                        f"Skipping malformed JSONL row (preview={preview!r})"
                    )
                    continue
                try:
                    snapshot = json.loads(line)
                    snapshots.append(snapshot)
                except json.JSONDecodeError as e:
                    preview = line[:120]
                    print(
                        f"[{datetime.now().isoformat()}] Error parsing line {line_num}: {e}. "
                        f"Skipping malformed JSONL row (preview={preview!r})"
                    )
                    continue
    except (IOError, OSError) as e:
        print(f"[{datetime.now().isoformat()}] Error reading JSONL file: {e}")

    return snapshots


def repair_snapshots_jsonl(filepath):
    """
    Remove malformed JSONL lines from snapshots file.
    Returns dict with total/kept/removed counts and optional backup_path.
    """
    stats = {'total': 0, 'kept': 0, 'removed': 0, 'backup_path': None}
    if not os.path.exists(filepath):
        return stats

    temp_path = filepath + '.repair.tmp'
    lock_path = filepath + '.lock'
    lock_file = None
    try:
        lock_file = _acquire_file_lock(lock_path)
        with open(filepath, 'r') as src, open(temp_path, 'w') as dst:
            for line in src:
                stripped = line.strip()
                if not stripped:
                    continue
                stats['total'] += 1
                if '\x00' in stripped:
                    stats['removed'] += 1
                    continue
                try:
                    json.loads(stripped)
                    dst.write(stripped + '\n')
                    stats['kept'] += 1
                except json.JSONDecodeError:
                    stats['removed'] += 1

        if stats['removed'] > 0:
            backup_path = filepath + f".backup.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            shutil.copy2(filepath, backup_path)
            stats['backup_path'] = backup_path
            os.replace(temp_path, filepath)
            print(
                f"[{datetime.now().isoformat()}] Repaired JSONL snapshots: "
                f"removed {stats['removed']} malformed line(s), kept {stats['kept']}, "
                f"backup saved to {backup_path}"
            )
        elif os.path.exists(temp_path):
            os.remove(temp_path)
    except (IOError, OSError) as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        print(f"[{datetime.now().isoformat()}] Error repairing JSONL file: {e}")
    finally:
        if lock_file is not None:
            _release_file_lock(lock_file)

    return stats


def append_snapshot_jsonl(filepath, snapshot):
    """
    Append a single snapshot to JSONL file safely.
    Uses a file lock + append + fsync to prevent inter-process corruption.
    """
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    lock_path = filepath + '.lock'
    lock_file = None
    try:
        lock_file = _acquire_file_lock(lock_path)
        line = json.dumps(snapshot, separators=(',', ':')) + '\n'
        with open(filepath, 'a') as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        return True

    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Error appending to JSONL: {e}")
        raise
    finally:
        if lock_file is not None:
            _release_file_lock(lock_file)


def _open_jsonl(filepath):
    """Open a JSONL file, preferring .gz version if it exists."""
    gz_path = filepath + '.gz'
    if os.path.exists(gz_path):
        return gzip.open(gz_path, 'rt', encoding='utf-8')
    elif os.path.exists(filepath):
        return open(filepath, 'r')
    return None

def count_snapshots_jsonl(filepath):
    """Count total valid snapshots in JSONL file without loading all into memory"""
    f = _open_jsonl(filepath)
    if f is None:
        return 0

    count = 0
    with f:
        for line in f:
            stripped = line.strip()
            if stripped and '\x00' not in stripped:
                count += 1
    return count

def count_data_points_jsonl(filepath):
    """Count total data points (candidates across all snapshots) in JSONL file"""
    f = _open_jsonl(filepath)
    if f is None:
        return 0

    total_data_points = 0
    with f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    snapshot = json.loads(line)
                    candidates = snapshot.get('candidates', [])
                    total_data_points += len(candidates)
                except:
                    pass  # Skip malformed lines
    return total_data_points

# ===== TIMESTAMP PARSING =====

def parse_snapshot_timestamp(ts_str):
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
    return None


# ===== RAMER-DOUGLAS-PEUCKER SIMPLIFICATION =====

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

_jsonl_lines_cache = {'size': None, 'lines': None}
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


def get_jsonl_raw_lines():
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
    return lines


def _get_compute_lock(cache_key):
    """Get or create a per-key lock for thundering-herd protection."""
    with _chart_compute_locks_lock:
        if cache_key not in _chart_compute_locks:
            _chart_compute_locks[cache_key] = _threading.Lock()
        return _chart_compute_locks[cache_key]

# ===== EMAIL ALERT FUNCTIONS =====

def read_subscribers():
    """Read subscriber list from JSONL file. Returns list of {email, subscribed_at}."""
    subscribers = []
    if not os.path.exists(SUBSCRIBERS_PATH):
        return subscribers
    try:
        with open(SUBSCRIBERS_PATH, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    subscribers.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (IOError, OSError):
        pass
    return subscribers

def add_subscriber(email, threshold=5.0):
    """Add a subscriber. Returns unsub token. Raises ValueError if duplicate."""
    email = email.lower().strip()
    threshold = float(threshold) if threshold else 5.0

    # Validate threshold range
    if threshold < 1.0 or threshold > 20.0:
        raise ValueError('Threshold must be between 1% and 20%')

    existing = read_subscribers()
    for sub in existing:
        if sub.get('email') == email:
            raise ValueError('Already subscribed')

    record = {
        'email': email,
        'threshold': threshold,
        'subscribed_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    }

    os.makedirs(os.path.dirname(SUBSCRIBERS_PATH), exist_ok=True)
    with open(SUBSCRIBERS_PATH, 'a') as f:
        f.write(json.dumps(record) + '\n')

    return make_unsub_token(email)

def remove_subscriber(email):
    """Remove a subscriber by rewriting JSONL without that email."""
    email = email.lower().strip()
    if not os.path.exists(SUBSCRIBERS_PATH):
        return False

    kept = []
    found = False
    with open(SUBSCRIBERS_PATH, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if record.get('email') == email:
                    found = True
                    continue
                kept.append(line)
            except json.JSONDecodeError:
                kept.append(line)

    if found:
        with open(SUBSCRIBERS_PATH, 'w') as f:
            for line in kept:
                f.write(line + '\n')
    return found

def make_unsub_token(email):
    """Generate unsubscribe token: sha256(email:salt)[:16]"""
    return hashlib.sha256(f"{email.lower().strip()}:{EMAIL_SECRET_SALT}".encode()).hexdigest()[:16]

def verify_unsub_token(email, token):
    """Verify an unsubscribe token matches."""
    return make_unsub_token(email) == token

def send_email(to, subject, html, text=None):
    """Send email via Resend API. Returns True on success."""
    if not RESEND_API_KEY:
        print(f"[{datetime.now().isoformat()}] Email skipped (no RESEND_API_KEY): {subject} -> {to}")
        return False
    try:
        payload = {
            'from': RESEND_FROM,
            'to': [to],
            'subject': subject,
            'html': html
        }
        if text:
            payload['text'] = text

        resp = requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json'
            },
            json=payload,
            timeout=10
        )
        if resp.status_code in (200, 201):
            print(f"[{datetime.now().isoformat()}] Email sent: {subject} -> {to}")
            return True
        else:
            print(f"[{datetime.now().isoformat()}] Email failed ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Email error: {e}")
        return False

BACKUP_EMAIL = os.environ.get('BACKUP_EMAIL', 'rymccomb1@icloud.com')

def send_csv_backup_email():
    """Send CSV backup of all historical data via Resend with attachment."""
    import io, csv, base64
    try:
        snapshots = read_snapshots_jsonl(HISTORICAL_DATA_PATH)
        if not snapshots:
            print(f"[{datetime.now().isoformat()}] CSV backup skipped: no data")
            return False

        # Build CSV
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['timestamp', 'candidate', 'probability', 'hasKalshi', 'interpolated'])
        for snapshot in snapshots:
            timestamp = snapshot.get('timestamp', '')
            is_interpolated = 'true' if snapshot.get('interpolated', False) else 'false'
            for candidate in snapshot.get('candidates', []):
                name = candidate.get('name', '')
                prob = _safe_float(candidate.get('probability', 0), 0.0)
                has_kalshi = 'true' if candidate.get('hasKalshi', False) else 'false'
                writer.writerow([timestamp, name, f'{prob:.1f}', has_kalshi, is_interpolated])
        csv_content = output.getvalue()
        output.close()

        snap_count = len(snapshots)
        first_ts = snapshots[0].get('timestamp', 'unknown') if snapshots else 'none'
        last_ts = snapshots[-1].get('timestamp', 'unknown') if snapshots else 'none'
        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M')

        # Resend attachment: base64-encoded CSV
        csv_b64 = base64.b64encode(csv_content.encode('utf-8')).decode('utf-8')

        if not RESEND_API_KEY:
            print(f"[{datetime.now().isoformat()}] CSV backup email skipped (no RESEND_API_KEY)")
            return False

        payload = {
            'from': RESEND_FROM,
            'to': [BACKUP_EMAIL],
            'subject': f'IL9Cast Data Backup - {now_str} ({snap_count} snapshots)',
            'html': (
                f'<h3>IL9Cast Automated Data Backup</h3>'
                f'<p><strong>Snapshots:</strong> {snap_count}</p>'
                f'<p><strong>Range:</strong> {first_ts} → {last_ts}</p>'
                f'<p><strong>CSV Size:</strong> {len(csv_content):,} bytes</p>'
                f'<p>Attached: <code>il9cast_backup_{now_str}.csv</code></p>'
                f'<hr><p style="color:#888;font-size:12px">Sent automatically every 4 hours from IL9Cast</p>'
            ),
            'attachments': [{
                'filename': f'il9cast_backup_{now_str}.csv',
                'content': csv_b64
            }]
        }

        resp = requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json'
            },
            json=payload,
            timeout=30
        )
        if resp.status_code in (200, 201):
            print(f"[{datetime.now().isoformat()}] CSV backup sent to {BACKUP_EMAIL} ({snap_count} snapshots, {len(csv_content):,} bytes)")
            return True
        else:
            print(f"[{datetime.now().isoformat()}] CSV backup email failed ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] CSV backup email error: {e}")
        return False


def send_welcome_email(email, threshold=5.0):
    """Send welcome email to new subscriber."""
    token = make_unsub_token(email)
    unsub_url = f"{SITE_BASE_URL}unsubscribe?email={email}&token={token}"

    # Plain text version
    text = f"""
Welcome to IL9Cast Alerts!

You'll now receive:

⚡ Big Swing Alerts
Get notified immediately when any candidate moves {threshold:.1f}%+ in the prediction markets

📊 Daily Summary
Every morning at 8 AM CT: current standings and 24-hour changes

View Live Markets: {SITE_BASE_URL}markets

---
Unsubscribe: {unsub_url}
    """

    # HTML version
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="margin: 0; padding: 0; background-color: #1A1A1E; font-family: 'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E;">
            <tr><td align="center" style="padding: 40px 20px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width: 600px; background-color: #232328; border: 1px solid #31B0B5;">
                    <!-- Logo -->
                    <tr><td style="padding: 32px 40px 0 40px; text-align: center; border-bottom: 1px solid #2a2a30;">
                        <h1 style="margin: 0 0 6px 0; font-family: Georgia, 'Times New Roman', serif; font-size: 28px; font-weight: 400; letter-spacing: 1px;">
                            <span style="color: #F0EFEB;">IL9</span><span style="color: #31B0B5;">Cast</span>
                        </h1>
                        <p style="margin: 0 0 20px 0; color: #888; font-size: 11px; letter-spacing: 2px; text-transform: uppercase;">Alert System Activated</p>
                    </td></tr>

                    <!-- Welcome -->
                    <tr><td style="padding: 32px 40px 24px 40px; text-align: center;">
                        <h2 style="margin: 0 0 8px 0; color: #F0EFEB; font-family: Georgia, 'Times New Roman', serif; font-size: 22px; font-weight: 400;">Welcome</h2>
                        <p style="margin: 0; color: #888; font-size: 14px; line-height: 1.6;">You're now subscribed to IL-9 primary race alerts.</p>
                    </td></tr>

                    <!-- Features -->
                    <tr><td style="padding: 0 40px 12px 40px;">
                        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E; border: 1px solid #2a2a30;">
                            <tr><td style="padding: 20px 24px;">
                                <h3 style="margin: 0 0 6px 0; color: #31B0B5; font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Big Swing Alerts</h3>
                                <p style="margin: 0; color: #999; font-size: 14px; line-height: 1.5;">Notified when any candidate moves {threshold:.1f}%+ in prediction markets</p>
                            </td></tr>
                        </table>
                    </td></tr>
                    <tr><td style="padding: 0 40px 24px 40px;">
                        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E; border: 1px solid #2a2a30;">
                            <tr><td style="padding: 20px 24px;">
                                <h3 style="margin: 0 0 6px 0; color: #31B0B5; font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Daily Summary</h3>
                                <p style="margin: 0; color: #999; font-size: 14px; line-height: 1.5;">Every morning at 8 AM CT: standings and 24-hour changes</p>
                            </td></tr>
                        </table>
                    </td></tr>

                    <!-- CTA -->
                    <tr><td style="padding: 8px 40px 32px 40px; text-align: center;">
                        <a href="{SITE_BASE_URL}markets" style="display: inline-block; background-color: #31B0B5; color: #ffffff; text-decoration: none; padding: 12px 32px; font-weight: 600; font-size: 15px;">View Live Markets</a>
                    </td></tr>

                    <!-- Footer -->
                    <tr><td style="padding: 20px 40px; text-align: center; border-top: 1px solid #2a2a30;">
                        <p style="margin: 0; color: #555; font-size: 11px;"><a href="{unsub_url}" style="color: #555; text-decoration: underline;">Unsubscribe</a></p>
                    </td></tr>
                </table>
            </td></tr>
        </table>
    </body>
    </html>
    """
    send_email(email, 'Welcome to IL9Cast Alerts', html, text)

def check_swings_and_alert(new_snapshot, prev_snapshot):
    """Compare snapshots and send alerts based on each subscriber's threshold. 60-min debounce per candidate."""
    if not prev_snapshot:
        return

    prev_by_name = {c['name']: c['probability'] for c in prev_snapshot.get('candidates', [])}
    now_ts = _time.time()

    # Calculate all deltas
    all_swings = []
    for c in new_snapshot.get('candidates', []):
        name = c['name']
        new_prob = c['probability']
        old_prob = prev_by_name.get(name)
        if old_prob is None:
            continue
        delta = new_prob - old_prob
        if abs(delta) >= 1.0:  # Only track swings >= 1% (minimum threshold)
            all_swings.append({
                'name': name,
                'old': old_prob,
                'new': new_prob,
                'delta': delta
            })

    if not all_swings:
        return

    # Send alerts to each subscriber based on their threshold
    subscribers = read_subscribers()
    for sub in subscribers:
        email = sub['email']
        threshold = sub.get('threshold', 5.0)

        # Filter swings that meet this subscriber's threshold
        subscriber_swings = []
        for swing in all_swings:
            if abs(swing['delta']) >= threshold:
                # Check 60-minute debounce (per candidate, globally)
                last_alert = _swing_debounce.get(swing['name'], 0)
                if now_ts - last_alert < 3600:
                    continue
                subscriber_swings.append(swing)

        if subscriber_swings:
            # Update debounce for all candidates we're alerting about
            for swing in subscriber_swings:
                _swing_debounce[swing['name']] = now_ts
            send_swing_alert_to_subscriber(email, subscriber_swings)

def send_swing_alert_to_subscriber(email, swings):
    """Build and send swing alert email to a single subscriber."""
    if not swings:
        return

    # Build plain text version
    text_rows = []
    for s in swings:
        arrow = '▲' if s['delta'] > 0 else '▼'
        text_rows.append(f"{s['name']}: {s['old']:.1f}% → {s['new']:.1f}% ({arrow} {abs(s['delta']):.1f}%)")

    text = f"""
IL9Cast Big Swing Alert!

{chr(10).join(text_rows)}

View Live Markets: {SITE_BASE_URL}markets
    """

    # Build HTML rows
    rows = ''
    for s in swings:
        arrow = '▲' if s['delta'] > 0 else '▼'
        color = '#31B686' if s['delta'] > 0 else '#e74c3c'
        rows += f"""
                                <tr>
                                    <td style="padding: 14px; border-bottom: 1px solid #2a2a30; color: #F0EFEB; font-weight: 500;">{s['name']}</td>
                                    <td style="padding: 14px; border-bottom: 1px solid #2a2a30; color: #888;">{s['old']:.1f}%</td>
                                    <td style="padding: 14px; border-bottom: 1px solid #2a2a30; color: #31B0B5; font-weight: 600;">{s['new']:.1f}%</td>
                                    <td style="padding: 14px; border-bottom: 1px solid #2a2a30; color: {color}; font-weight: 700; font-size: 16px;">
                                        {arrow} {abs(s['delta']):.1f}%
                                    </td>
                                </tr>"""

    subject = f"⚡ IL9Cast Alert: {swings[0]['name']} {'+' if swings[0]['delta'] > 0 else ''}{swings[0]['delta']:.1f}%"
    if len(swings) > 1:
        subject = f"⚡ IL9Cast Alert: {len(swings)} candidates moved significantly"

    token = make_unsub_token(email)
    unsub_url = f"{SITE_BASE_URL}unsubscribe?email={email}&token={token}"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="margin: 0; padding: 0; background-color: #1A1A1E; font-family: 'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E;">
            <tr><td align="center" style="padding: 40px 20px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width: 600px; background-color: #232328; border: 1px solid #31B0B5;">
                    <!-- Logo -->
                    <tr><td style="padding: 32px 40px 0 40px; text-align: center; border-bottom: 1px solid #2a2a30;">
                        <h1 style="margin: 0 0 6px 0; font-family: Georgia, 'Times New Roman', serif; font-size: 28px; font-weight: 400; letter-spacing: 1px;">
                            <span style="color: #F0EFEB;">IL9</span><span style="color: #31B0B5;">Cast</span>
                        </h1>
                        <p style="margin: 0 0 20px 0; color: #31B0B5; font-size: 11px; letter-spacing: 2px; text-transform: uppercase; font-weight: 700;">Market Movement Detected</p>
                    </td></tr>

                    <!-- Data Table -->
                    <tr><td style="padding: 28px 40px;">
                        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E; border: 1px solid #2a2a30;">
                            <thead>
                                <tr style="background-color: #1A1A1E;">
                                    <th style="text-align: left; padding: 12px 14px; color: #888; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 1px solid #2a2a30;">Candidate</th>
                                    <th style="text-align: left; padding: 12px 14px; color: #888; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 1px solid #2a2a30;">Before</th>
                                    <th style="text-align: left; padding: 12px 14px; color: #888; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 1px solid #2a2a30;">After</th>
                                    <th style="text-align: left; padding: 12px 14px; color: #888; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 1px solid #2a2a30;">Change</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows}
                            </tbody>
                        </table>
                    </td></tr>

                    <!-- CTA -->
                    <tr><td style="padding: 0 40px 32px 40px; text-align: center;">
                        <a href="{SITE_BASE_URL}markets" style="display: inline-block; background-color: #31B0B5; color: #ffffff; text-decoration: none; padding: 12px 32px; font-weight: 600; font-size: 15px;">View Live Markets</a>
                    </td></tr>

                    <!-- Footer -->
                    <tr><td style="padding: 20px 40px; text-align: center; border-top: 1px solid #2a2a30;">
                        <p style="margin: 0; color: #555; font-size: 11px;"><a href="{unsub_url}" style="color: #555; text-decoration: underline;">Unsubscribe</a></p>
                    </td></tr>
                </table>
            </td></tr>
        </table>
    </body>
    </html>
    """
    send_email(email, subject, html, text)

def send_daily_summary():
    """Send daily summary email with current standings and 24h changes."""
    global _daily_summary_sent
    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if _daily_summary_sent == today_str:
        return
    _daily_summary_sent = today_str

    subscribers = read_subscribers()
    if not subscribers:
        return

    snapshots = read_snapshots_jsonl(HISTORICAL_DATA_PATH)
    if not snapshots:
        return

    current = snapshots[-1]
    now_utc = datetime.now(timezone.utc)
    cutoff_24h = now_utc - timedelta(hours=24)

    # Find snapshot closest to 24h ago
    old_snapshot = None
    for snap in snapshots:
        dt = parse_snapshot_timestamp(snap.get('timestamp', ''))
        if dt and dt <= cutoff_24h:
            old_snapshot = snap

    old_by_name = {}
    if old_snapshot:
        old_by_name = {c['name']: c['probability'] for c in old_snapshot.get('candidates', [])}

    rows = ''
    for c in sorted(current.get('candidates', []), key=lambda x: x['probability'], reverse=True):
        name = c['name']
        prob = c['probability']
        old_prob = old_by_name.get(name)
        if old_prob is not None:
            delta = prob - old_prob
            arrow = '▲' if delta > 0 else ('▼' if delta < 0 else '—')
            color = '#31B686' if delta > 0 else ('#e74c3c' if delta < 0 else '#888')
            if delta != 0:
                change_str = f'{arrow} {abs(delta):.1f}%'
            else:
                change_str = '—'
        else:
            color = '#888'
            change_str = 'New'

        rows += f"""
                                            <tr>
                                                <td style="padding: 14px; border-bottom: 1px solid #2a2a30; color: #F0EFEB; font-weight: 500;">{name}</td>
                                                <td style="padding: 14px; border-bottom: 1px solid #2a2a30; color: #31B0B5; font-weight: 700; font-size: 16px;">{prob:.1f}%</td>
                                                <td style="padding: 14px; border-bottom: 1px solid #2a2a30; color: {color}; font-weight: 600;">{change_str}</td>
                                            </tr>"""

    ct_time = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-6)))
    date_str = ct_time.strftime('%B %d, %Y')

    # Plain text version
    text_rows = []
    for c in sorted(current.get('candidates', []), key=lambda x: x['probability'], reverse=True):
        name = c['name']
        prob = c['probability']
        old_prob = old_by_name.get(name)
        if old_prob is not None:
            delta = prob - old_prob
            arrow = '▲' if delta > 0 else ('▼' if delta < 0 else '—')
            text_rows.append(f"{name}: {prob:.1f}% ({arrow} {abs(delta):.1f}% 24h)")
        else:
            text_rows.append(f"{name}: {prob:.1f}% (New)")

    text = f"""
IL9Cast Daily Summary - {date_str}

{chr(10).join(text_rows)}

View Live Markets: {SITE_BASE_URL}markets
    """

    for sub in subscribers:
        email = sub['email']
        token = make_unsub_token(email)
        unsub_url = f"{SITE_BASE_URL}unsubscribe?email={email}&token={token}"

        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
        <body style="margin: 0; padding: 0; background-color: #1A1A1E; font-family: 'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E;">
                <tr><td align="center" style="padding: 40px 20px;">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width: 600px; background-color: #232328; border: 1px solid #31B0B5;">
                        <!-- Logo -->
                        <tr><td style="padding: 32px 40px 0 40px; text-align: center; border-bottom: 1px solid #2a2a30;">
                            <h1 style="margin: 0 0 6px 0; font-family: Georgia, 'Times New Roman', serif; font-size: 28px; font-weight: 400; letter-spacing: 1px;">
                                <span style="color: #F0EFEB;">IL9</span><span style="color: #31B0B5;">Cast</span>
                            </h1>
                            <p style="margin: 0 0 20px 0; color: #888; font-size: 11px; letter-spacing: 2px; text-transform: uppercase;">Daily Summary &middot; {date_str}</p>
                        </td></tr>

                        <!-- Data Table -->
                        <tr><td style="padding: 28px 40px;">
                            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E; border: 1px solid #2a2a30;">
                                <thead>
                                    <tr>
                                        <th style="text-align: left; padding: 12px 14px; color: #888; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 1px solid #2a2a30;">Candidate</th>
                                        <th style="text-align: left; padding: 12px 14px; color: #888; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 1px solid #2a2a30;">Current</th>
                                        <th style="text-align: left; padding: 12px 14px; color: #888; font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 1px solid #2a2a30;">24h Change</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {rows}
                                </tbody>
                            </table>
                        </td></tr>

                        <!-- CTA -->
                        <tr><td style="padding: 0 40px 32px 40px; text-align: center;">
                            <a href="{SITE_BASE_URL}markets" style="display: inline-block; background-color: #31B0B5; color: #ffffff; text-decoration: none; padding: 12px 32px; font-weight: 600; font-size: 15px;">View Live Markets</a>
                        </td></tr>

                        <!-- Footer -->
                        <tr><td style="padding: 20px 40px; text-align: center; border-top: 1px solid #2a2a30;">
                            <p style="margin: 0; color: #555; font-size: 11px;"><a href="{unsub_url}" style="color: #555; text-decoration: underline;">Unsubscribe</a></p>
                        </td></tr>
                    </table>
                </td></tr>
            </table>
        </body>
        </html>
        """
        send_email(email, f'IL9Cast Daily Summary - {date_str}', html, text)

    print(f"[{datetime.now().isoformat()}] Daily summary sent to {len(subscribers)} subscriber(s)")


# ===== FEC API FUNCTIONS =====

def fetch_all_fec_data():
    """
    Returns hardcoded FEC data for all IL-09 2026 candidates.

    Source: Pre-Primary FEC filings (coverage through Feb 25, 2026).
    Filed March 5, 2026. Retrieved March 6, 2026.

    Field definitions:
      - total_raised: Cumulative receipts (FEC Line 11e, Column B - total)
      - total_spent: Cumulative disbursements (FEC Line 22, Column B - total)
      - cash_on_hand: FEC-reported COH (Line 27). May differ from
        total_raised - total_spent due to beginning balance, loans, refunds.
      - total_donors: Estimated donor count from contribution data
      - small_dollar_amount: Unitemized individual contributions (under $200)
      - individual_total: Total individual contributions (itemized + unitemized)
      - burn_rate_monthly: Period disbursements (Column A, Jan 1-Feb 25 only,
        56 days) converted to monthly: amount / (56 / 30.44).
        Uses period-specific disbursements, NOT cumulative total_spent.
      - raise_rate_monthly: Period receipts (Column A, Jan 1-Feb 25 only,
        56 days) converted to monthly: amount / (56 / 30.44).
        Uses period-specific receipts, NOT cumulative total_raised.
      - cash_runway_months: cash_on_hand / burn_rate_monthly
      - spent_pct_of_raised: (total_spent / total_raised) * 100
      - avg_contribution: individual_total / total_donors
      - small_dollar_pct: (small_dollar_amount / individual_total) * 100
    """
    return [
        {
            "name": "Daniel Biss",
            "total_raised": 2539961.32,
            "total_spent": 1894041.97,
            "cash_on_hand": 645919.35,
            "total_donors": 5590,
            "small_dollar_amount": 127957.89,
            "individual_total": 2425950.53,
            "coverage_end_date": "2026-02-25T00:00:00",
            "committee_id": "C00905307",
            "burn_rate_monthly": 698917,
            "raise_rate_monthly": 301912,
            "cash_runway_months": 0.9,
            "burn_period_label": "Jan 1 – Feb 25, 2026",
            "spent_pct_of_raised": 74.57,
            "avg_contribution": 433.98,
            "small_dollar_pct": 5.27
        },
        {
            "name": "Kat Abugazaleh",
            "total_raised": 3359172.06,
            "total_spent": 2977254.36,
            "cash_on_hand": 382621.26,  # FEC Line 27 (differs from receipts-disbursements by $703.56 due to prior balance)
            "total_donors": 49100,
            "small_dollar_amount": 2247721.60,
            "individual_total": 3356755.42,
            "coverage_end_date": "2026-02-25T00:00:00",
            "committee_id": "C00900449",
            "burn_rate_monthly": 588811,
            "raise_rate_monthly": 355958,
            "cash_runway_months": 0.6,
            "burn_period_label": "Jan 1 – Feb 25, 2026",
            "spent_pct_of_raised": 88.63,
            "avg_contribution": 68.37,
            "small_dollar_pct": 66.96
        },
        {
            "name": "Laura Fine",
            "total_raised": 2555781.35,
            "total_spent": 2095128.95,
            "cash_on_hand": 461679.43,  # FEC Line 27 (differs from receipts-disbursements by $1,027.03 due to prior balance)
            "total_donors": 6443,
            "small_dollar_amount": 76381.76,
            "individual_total": 2517581.35,
            "coverage_end_date": "2026-02-25T00:00:00",
            "committee_id": "C00904326",
            "burn_rate_monthly": 877360,
            "raise_rate_monthly": 345467,
            "cash_runway_months": 0.5,
            "burn_period_label": "Jan 1 – Feb 25, 2026",
            "spent_pct_of_raised": 81.98,
            "avg_contribution": 390.75,
            "small_dollar_pct": 3.03
        },
        {
            "name": "Mike Simmons",
            "total_raised": 414048.31,
            "total_spent": 278898.27,
            "cash_on_hand": 135150.04,
            "total_donors": 1384,
            "small_dollar_amount": 60601.58,
            "individual_total": 393748.31,
            "coverage_end_date": "2026-02-25T00:00:00",
            "committee_id": "C00910976",
            "burn_rate_monthly": 48470,
            "raise_rate_monthly": 48469,
            "cash_runway_months": 2.8,
            "burn_period_label": "Jan 1 – Feb 25, 2026",
            "spent_pct_of_raised": 67.36,
            "avg_contribution": 284.50,
            "small_dollar_pct": 15.39
        },
        {
            "name": "Phil Andrew",
            "total_raised": 1339123.10,
            "total_spent": 1166047.75,
            "cash_on_hand": 173075.35,
            "total_donors": 2367,
            "small_dollar_amount": 65993.06,
            "individual_total": 926762.57,
            "coverage_end_date": "2026-02-25T00:00:00",
            "committee_id": "C00911024",
            "burn_rate_monthly": 498266,
            "raise_rate_monthly": 69759,
            "cash_runway_months": 0.3,
            "burn_period_label": "Jan 1 – Feb 25, 2026",
            "spent_pct_of_raised": 87.08,
            "avg_contribution": 391.53,
            "small_dollar_pct": 7.12
        }
    ]


# ===== INITIALIZATION =====

def initialize_data():
    """
    Initialize the data directory and seed from backup if needed.
    On Railway: copies seed data to persistent volume on first deploy.
    Migrates from legacy JSON format to JSONL if needed.
    """
    data_dir = os.path.dirname(HISTORICAL_DATA_PATH)
    os.makedirs(data_dir, exist_ok=True)

    # Migrate from legacy JSON to JSONL if needed
    if os.path.exists(LEGACY_JSON_PATH) and not os.path.exists(HISTORICAL_DATA_PATH):
        print(f"[{datetime.now().isoformat()}] Migrating from JSON to JSONL format...")
        try:
            with open(LEGACY_JSON_PATH, 'r') as f:
                legacy_data = json.load(f)

            if isinstance(legacy_data, list):
                with open(HISTORICAL_DATA_PATH, 'w') as f:
                    for snapshot in legacy_data:
                        f.write(json.dumps(snapshot) + '\n')
                print(f"[{datetime.now().isoformat()}] Migrated {len(legacy_data)} snapshots to JSONL")

                # Backup legacy file
                backup_path = LEGACY_JSON_PATH + '.pre-jsonl-backup'
                if not os.path.exists(backup_path):
                    shutil.copy2(LEGACY_JSON_PATH, backup_path)
                    print(f"[{datetime.now().isoformat()}] Legacy JSON backed up to {backup_path}")
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Error migrating to JSONL: {e}")

    # Only seed data if historical file doesn't exist at all
    # Once Railway starts collecting, never overwrite its data
    has_data = count_snapshots_jsonl(HISTORICAL_DATA_PATH) > 0
    if not has_data and os.path.exists(SEED_DATA_PATH):
        print(f"[{datetime.now().isoformat()}] Seeding data from {SEED_DATA_PATH}")
        try:
            with open(SEED_DATA_PATH, 'r') as src:
                seed_data = json.load(src)

            if isinstance(seed_data, list):
                with open(HISTORICAL_DATA_PATH, 'w') as dst:
                    for snapshot in seed_data:
                        dst.write(json.dumps(snapshot) + '\n')
                print(f"[{datetime.now().isoformat()}] Seeded {len(seed_data)} snapshots in JSONL format")
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Error seeding data: {e}")


def import_repo_csv_to_volume_if_needed(csv_path=REPO_CSV_PATH, output_path=HISTORICAL_DATA_PATH):
    """Bootstrap from repo CSV into JSONL, with volume-wipe detection via marker file.

    Uses a '.csv_recovery_done' marker on the persistent volume.  When the volume
    is wiped the marker disappears too, so the next restart triggers a full recovery
    (CSV import + bridge interpolation to surviving data + bridge to present).
    """
    marker = os.path.join(os.path.dirname(output_path), '.csv_recovery_done')

    if not os.path.exists(csv_path):
        return {'imported': False, 'reason': 'csv_missing', 'csv_path': csv_path, 'output_path': output_path}

    # Marker present → skip only if snapshots actually exist (handles stale/git markers)
    if os.path.exists(marker):
        if count_snapshots_jsonl(output_path) > 0:
            return {'imported': False, 'reason': 'already_recovered', 'csv_path': csv_path, 'output_path': output_path}
        try:
            os.remove(marker)
            print(f"[{datetime.now().isoformat()}] Removed stale recovery marker (no snapshots on disk)")
        except OSError:
            pass

    csv_snapshots = load_snapshots_from_csv(csv_path)
    if not csv_snapshots:
        return {'imported': False, 'reason': 'csv_empty', 'csv_path': csv_path, 'output_path': output_path}

    csv_count = len(csv_snapshots)
    existing_count = count_snapshots_jsonl(output_path)

    # If JSONL already has more data than CSV and is healthy, just create marker
    if existing_count >= csv_count:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, 'w') as f:
            f.write(f"healthy volume detected at {datetime.now(timezone.utc).isoformat()}\n")
        print(f"[{datetime.now().isoformat()}] Volume healthy ({existing_count} snapshots >= {csv_count} CSV). Created recovery marker.")
        return {'imported': False, 'reason': 'output_has_sufficient_data', 'existing_snapshots': existing_count}

    # ---- Recovery needed ----
    print(f"[{datetime.now().isoformat()}] Recovery needed: JSONL has {existing_count} snapshots, CSV has {csv_count}")
    stats = {}

    if existing_count > 0:
        # Volume wipe with some surviving post-wipe data → stitch CSV + bridge + surviving
        try:
            stats = recover_snapshots_from_csv_and_current(
                csv_path=csv_path,
                current_path=output_path,
                output_path=output_path,
                bridge_interval_minutes=3,
                max_bridge_hours=72,
                dry_run=False,
                csv_only=False
            )
            stats['reason'] = 'volume_wipe_recovery'
            print(f"[{datetime.now().isoformat()}] Stitched recovery: {stats.get('merged_total', 0)} total snapshots "
                  f"(CSV={stats.get('csv_snapshots', 0)}, current={stats.get('current_snapshots', 0)})")
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Stitched recovery failed ({e}). Falling through to CSV-only import.")
            existing_count = 0  # Force CSV-only path below

    if existing_count == 0:
        # No surviving data (or stitched recovery failed) → fresh CSV import
        lock_path = output_path + '.lock'
        lock_file = _acquire_file_lock(lock_path)
        tmp_path = output_path + '.import_tmp'
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(tmp_path, 'w') as f:
                for snap in csv_snapshots:
                    f.write(json.dumps(snap, separators=(',', ':')) + '\n')
            os.replace(tmp_path, output_path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            _release_file_lock(lock_file)
        stats = {'reason': 'csv_only_import', 'snapshots_written': csv_count}
        print(f"[{datetime.now().isoformat()}] CSV-only import: wrote {csv_count} snapshots")

    # Archive site: do not fabricate post-election snapshots unless ops explicitly enable.
    if os.environ.get('ENABLE_RECOVERY_BRIDGE', '').lower() in ('1', 'true', 'yes'):
        bridge_stats = bridge_to_present(output_path)
        stats['bridge_to_present'] = bridge_stats
        if bridge_stats.get('bridged'):
            print(f"[{datetime.now().isoformat()}] Bridged {bridge_stats['snapshots_added']} snapshots "
                  f"(gap was {bridge_stats['gap_hours']}h)")
    else:
        stats['bridge_to_present'] = {'bridged': False, 'reason': 'archive_mode_skip'}

    # Create recovery marker so we don't re-run on next restart
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, 'w') as f:
        f.write(f"recovered at {datetime.now(timezone.utc).isoformat()}\n")

    final_count = count_snapshots_jsonl(output_path)
    stats['imported'] = True
    stats['final_snapshot_count'] = final_count
    return stats

def purge_old_data():
    """
    Optional one-time purge: remove snapshots before Jan 30, 2026.
    Disabled by default to preserve historical volume data.
    Set ENABLE_PRE_JAN30_PURGE=true to run this migration.
    """
    purge_enabled = os.environ.get('ENABLE_PRE_JAN30_PURGE', '').strip().lower() in {'1', 'true', 'yes'}
    if not purge_enabled:
        print(f"[{datetime.now().isoformat()}] Pre-Jan30 purge disabled; preserving historical snapshots")
        return

    data_dir = os.path.dirname(HISTORICAL_DATA_PATH)
    marker = os.path.join(data_dir, '.purge_pre_jan30_done')
    if os.path.exists(marker):
        return

    if not os.path.exists(HISTORICAL_DATA_PATH):
        # Nothing to purge, but mark as done
        os.makedirs(data_dir, exist_ok=True)
        with open(marker, 'w') as f:
            f.write('done')
        return

    print(f"[{datetime.now().isoformat()}] Purging all data before Jan 30, 2026...")
    cutoff = datetime(2026, 1, 30, 0, 0, 0, tzinfo=timezone.utc)
    kept = []
    total = 0
    lock_path = HISTORICAL_DATA_PATH + '.lock'
    lock_file = None

    try:
        lock_file = _acquire_file_lock(lock_path)

        with open(HISTORICAL_DATA_PATH, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    snap = json.loads(line)
                    dt = parse_snapshot_timestamp(snap.get('timestamp', ''))
                    if dt and dt >= cutoff:
                        kept.append(line)
                except json.JSONDecodeError:
                    continue

        # Keep a safety backup before destructive rewrite
        backup_file(HISTORICAL_DATA_PATH, reason='purge-pre-jan30')

        # Rewrite file with only kept snapshots
        with open(HISTORICAL_DATA_PATH, 'w') as f:
            for line in kept:
                f.write(line + '\n')

        print(f"[{datetime.now().isoformat()}] Purged {total - len(kept)} old snapshots, kept {len(kept)}")

    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Error during purge: {e}")
    finally:
        if lock_file is not None:
            _release_file_lock(lock_file)

    # Also delete any legacy JSON files on Railway volume
    for pattern_dir in ['/data', '/app/data', os.path.join(os.path.dirname(__file__), 'data')]:
        for fname in ['historical_snapshots.json']:
            fpath = os.path.join(pattern_dir, fname)
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                    print(f"  Deleted legacy file: {fpath}")
                except Exception:
                    pass

    with open(marker, 'w') as f:
        f.write('done')

# Initialize data on module load
if os.environ.get('IL9_SKIP_STARTUP_TASKS', '').strip().lower() in {'1', 'true', 'yes'}:
    print(f"[{datetime.now().isoformat()}] Startup data tasks skipped via IL9_SKIP_STARTUP_TASKS")
else:
    print(f"[{datetime.now().isoformat()}] Using historical data path: {HISTORICAL_DATA_PATH}")
    initialize_data()
    import_result = import_repo_csv_to_volume_if_needed()
    if import_result.get('imported'):
        reason = import_result.get('reason', 'unknown')
        final = import_result.get('final_snapshot_count', '?')
        bridge_info = import_result.get('bridge_to_present', {})
        bridge_msg = f", bridged {bridge_info.get('snapshots_added', 0)} to present" if bridge_info.get('bridged') else ""
        print(f"[{datetime.now().isoformat()}] CSV recovery complete ({reason}): {final} total snapshots{bridge_msg}")
    else:
        print(f"[{datetime.now().isoformat()}] CSV import skipped: {import_result.get('reason', 'unknown')}")
    purge_old_data()
    repair_snapshots_jsonl(HISTORICAL_DATA_PATH)

# Real IL-9 Candidate Profiles
CANDIDATE_PROFILES = [
    {
        "name": "Daniel Biss",
        "slug": "daniel-biss",
        "title": "Mayor of Evanston",
        "photo": "images/candidates/biss.jpg",
        "campaign_url": "https://www.danielbiss.com",
        "bio": "Mayor of Evanston and former Illinois State Senator. Proven progressive with a legislative track record protecting healthcare, defending immigrants, and advocating for economic justice.",
        "endorsements": [
            "Rep. Jan Schakowsky",
            "Sen. Elizabeth Warren",
            "Illinois AFL-CIO",
            "SEIU Illinois State Council",
            "Illinois Federation of Teachers",
            "Congressional Progressive Caucus PAC"
        ],
        "key_issues": ["Medicare for All", "Wealth tax on billionaires", "Ban on mass deportations", "Cease-fire in Gaza"]
    },
    {
        "name": "Kat Abugazaleh",
        "slug": "kat-abugazaleh",
        "title": "Former Media Matters Researcher",
        "photo": "images/candidates/katabu.jpg",
        "campaign_url": "https://www.katforillinois.com/",
        "bio": "Media critic and researcher focused on combating right-wing disinformation. Running an anti-establishment campaign centered on breaking the status quo and transparent grassroots fundraising.",
        "endorsements": [
            "Rep. Ro Khanna",
            "Former Rep. Jamaal Bowman",
            "Sunrise Movement",
            "Peace Action"
        ],
        "key_issues": ["Rejecting corporate PAC money", "Media transparency", "Combating disinformation", "Breaking the status quo"]
    },
    {
        "name": "Laura Fine",
        "slug": "laura-fine",
        "title": "State Senator",
        "photo": "images/candidates/fine.jpg",
        "campaign_url": "https://www.laurafineforcongress.org/",
        "bio": "Illinois State Senator and champion for families. Recently passed laws banning prior authorization for mental health services, requiring insurance coverage for emergency neonatal intensive care, and mandating toxic metal testing in baby food.",
        "endorsements": [
            "Rep. Brad Schneider (IL-10)",
            "Rep. Lois Frankel (FL-22)",
            "Rep. Norma Torres (CA-25)",
            "State Rep. Tracy Katz Muhl (IL-57)",
            "State Sen. Laura Murphy (IL-28)",
            "Chicago Tribune",
            "Maine Township Democrats"
        ],
        "key_issues": ["Mental health access", "Insurance reform", "Family healthcare", "Toxic metal testing in baby food"]
    },
    {
        "name": "Mike Simmons",
        "slug": "mike-simmons",
        "title": "State Senator",
        "photo": "images/candidates/simmons.jpg",
        "campaign_url": "https://www.mikesimmons.org/",
        "bio": "First openly LGBTQ+ and Ethiopian-American Illinois State Senator. Passed the Jett Hawkins Act banning hair discrimination and championed the Patient and Provider Protection Act protecting gender-affirming care.",
        "endorsements": [
            "Equality PAC",
            "LGBTQ+ Victory Fund"
        ],
        "key_issues": ["Gender-affirming care", "Public transit expansion", "Affordable housing", "Permanent child tax credits"]
    },
    {
        "name": "Phil Andrew",
        "slug": "phil-andrew",
        "title": "Former FBI Agent",
        "photo": "images/candidates/philandrew.jpg",
        "campaign_url": "https://www.philandrewforcongress.com/",
        "bio": "Former FBI special agent and hostage negotiator with 21 years of service. Gun violence survivor shot by Laurie Dann in 1988, advocating for evidence-based community safety strategies.",
        "endorsements": [
            "Brady PAC"
        ],
        "key_issues": ["Gun violence prevention", "Community safety", "Political independence", "Refusing PAC money"]
    },
    # Bushra Amiwala removed — no pre-primary filing available
]

# Routes
@app.route('/healthz')
def healthz():
    """Lightweight health check for Railway (no template render)."""
    return jsonify({'status': 'ok'}), 200


@app.route('/')
def landing():
    return render_template('landing_new.html')

@app.route('/rjmc')
def rjmc_preview():
    return render_template('landing_new.html', force_election_night=True)

@app.route('/odds')
def odds():
    return render_template('odds.html')


@app.route('/api/model/precincts')
def api_model_precincts():
    """
    Serve precinct GeoJSON with gzip when supported (~1.6 MB -> ~250 KB).
    Cached immutably — geometry does not change on the archive site.
    """
    model_dir = os.path.join(app.static_folder, 'model')
    gz_path = os.path.join(model_dir, 'il9_precinct_model.geojson.gz')
    plain_path = os.path.join(model_dir, 'il9_precinct_model.geojson')
    accept = request.headers.get('Accept-Encoding', '').lower()
    if 'gzip' in accept and os.path.exists(gz_path):
        resp = send_file(gz_path, mimetype='application/geo+json')
        resp.headers['Content-Encoding'] = 'gzip'
        resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        resp.headers['Vary'] = 'Accept-Encoding'
        return resp
    if os.path.exists(plain_path):
        resp = send_file(plain_path, mimetype='application/geo+json')
        resp.headers['Cache-Control'] = 'public, max-age=86400'
        return resp
    return jsonify({'error': 'Precinct GeoJSON not found'}), 404


@app.route('/model/methodology')
def model_methodology():
    # Serve the corrected methodology source file directly to avoid binary asset swaps in PRs.
    pdf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docs', 'IL9Cast_Methodology_CORRECTED.pdf')
    return send_file(pdf_path, mimetype='application/pdf')

@app.route('/methodology')
def methodology():
    return render_template('methodology.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/markets')
def markets():
    return render_template('markets.html')

@app.route('/money')
def fundraising():
    return render_template('fundraising.html')

@app.route('/fundraising')
def fundraising_redirect():
    from flask import redirect
    return redirect('/money', code=301)

@app.route('/outside-money')
def outside_money():
    from flask import redirect
    return redirect('/money#independent-expenditures', code=301)


@app.route('/updates')
def updates():
    return render_template('updates.html')

@app.route('/case-study/bid-ask-spreads')
def case_study_bid_ask():
    return render_template('case_study_bid_ask.html')

# Certified final vote shares for the March 17, 2026 IL-9 Democratic primary.
# Top-three numbers are from Ballotpedia / AP reporting; candidates below that
# did not have cleanly reported per-candidate tallies in open sources, so they
# are shown as a grouped bucket rather than fabricated.
FINAL_VOTE_SHARES = {
    'Daniel Biss':        {'share': 29.6, 'label': '29.6%', 'sublabel': 'Final Vote Share',     'sort_rank': 1},
    'Kat Abugazaleh':     {'share': 25.9, 'label': '25.9%', 'sublabel': 'Final Vote Share',     'sort_rank': 2},
    'Laura Fine':         {'share': 20.4, 'label': '20.4%', 'sublabel': 'Final Vote Share',     'sort_rank': 3},
}


@app.route('/candidates')
def candidates():
    """Show candidate profiles with certified final vote shares."""
    candidates_data = []
    for profile in CANDIDATE_PROFILES:
        candidate = profile.copy()
        result = FINAL_VOTE_SHARES.get(candidate['name'])
        if result:
            candidate['current_odds'] = result['share']
            candidate['result_label'] = result['label']
            candidate['result_sublabel'] = result['sublabel']
            candidate['_sort_rank'] = result['sort_rank']
        else:
            # Outside the top 3; per-candidate tallies weren't cleanly reported.
            candidate['current_odds'] = 0.0
            candidate['result_label'] = '< 6%'
            candidate['result_sublabel'] = 'Below top 3'
            candidate['_sort_rank'] = 99
        candidate['has_kalshi'] = False
        candidates_data.append(candidate)

    # Top 3 in result order, then everyone else in profile order.
    candidates_data.sort(key=lambda x: (x['_sort_rank'], x.get('name', '')))

    return render_template('candidates.html', candidates=candidates_data)

@app.route('/sitemap.xml')
def sitemap():
    """Serve sitemap.xml for search engines"""
    from flask import Response
    return Response(render_template('sitemap.xml'), mimetype='application/xml')

@app.route('/robots.txt')
def robots():
    """Serve robots.txt for search engine crawlers"""
    from flask import Response
    return Response(render_template('robots.txt'), mimetype='text/plain')

@app.route('/money/<candidate_slug>')
def candidate_fundraising(candidate_slug):
    """Show individual candidate fundraising page"""
    # Find candidate profile
    candidate_profile = next((c for c in CANDIDATE_PROFILES if c['slug'] == candidate_slug), None)

    if not candidate_profile:
        return "Candidate not found", 404

    # Get latest snapshot for current odds
    snapshots = read_snapshots_jsonl(HISTORICAL_DATA_PATH)
    latest_snapshot = snapshots[-1] if snapshots else None

    # Add current odds
    candidate = candidate_profile.copy()
    if latest_snapshot:
        for c in latest_snapshot.get('candidates', []):
            snapshot_name = c['name'].replace('Abughazaleh', 'Abugazaleh')
            if snapshot_name == candidate['name']:
                candidate['current_odds'] = c['probability']
                candidate['has_kalshi'] = c.get('hasKalshi', False)
                break

    if 'current_odds' not in candidate:
        candidate['current_odds'] = 0.0
        candidate['has_kalshi'] = False

    # Get FEC data for this candidate
    all_fec_data = fetch_all_fec_data()
    fec_data = None
    for fec_candidate in all_fec_data:
        # Normalize names for matching
        fec_name = fec_candidate['name']
        candidate_name = candidate['name']
        if fec_name == candidate_name or fec_name.replace('Abugazaleh', 'Abughazaleh') == candidate_name:
            fec_data = fec_candidate
            break

    # Add FEC data to candidate object
    if fec_data:
        candidate.update(fec_data)

    return render_template('candidate_fundraising.html', candidate=candidate)

# API Endpoints

@app.route('/api/snapshot', methods=['POST'])
def save_snapshot():
    """Live collection ended; returns 410 for legacy clients."""
    return jsonify({'error': 'Live snapshot collection ended March 17, 2026'}), 410

# Archive mode: serve the final Manifold/Kalshi responses from snapshots bundled
# in the git repo. No live network calls — the site must keep working even if
# the external APIs change or go away.
_ARCHIVE_DIR = os.path.join(os.path.dirname(__file__), 'data', 'archive')

def _serve_archive_json(filename):
    path = os.path.join(_ARCHIVE_DIR, filename)
    try:
        with open(path, 'r') as f:
            data = json.load(f)
    except (IOError, OSError, ValueError) as e:
        return jsonify({"error": f"archive unavailable: {e}"}), 500
    result = jsonify(data)
    result.headers['Cache-Control'] = 'public, max-age=3600'
    return result

@app.route('/api/manifold')
def get_manifold():
    """Serve the final archived Manifold Markets response."""
    return _serve_archive_json('manifold.json')

@app.route('/api/kalshi')
def get_kalshi():
    """Serve the final archived Kalshi response (reshaped to legacy /markets format)."""
    path = os.path.join(_ARCHIVE_DIR, 'kalshi.json')
    try:
        with open(path, 'r') as f:
            data = json.load(f)
    except (IOError, OSError, ValueError) as e:
        return jsonify({"error": f"archive unavailable: {e}"}), 500
    markets = data.get('markets', [])
    for m in markets:
        if not m.get('subtitle'):
            m['subtitle'] = m.get('yes_sub_title') or m.get('custom_strike', {}).get('Candidate', '')
        # Kalshi switched to dollar-based string fields; normalise to legacy
        # cent-based numeric fields the front-end expects.
        if m.get('last_price') is None and m.get('last_price_dollars') is not None:
            try:
                m['last_price'] = round(float(m['last_price_dollars']) * 100, 1)
            except (ValueError, TypeError):
                m['last_price'] = 0
        if m.get('yes_bid') is None and m.get('no_ask_dollars') is not None:
            try:
                m['yes_bid'] = round((1.0 - float(m['no_ask_dollars'])) * 100, 1)
            except (ValueError, TypeError):
                m['yes_bid'] = 0
        if m.get('yes_ask') is None and m.get('no_bid_dollars') is not None:
            try:
                m['yes_ask'] = round((1.0 - float(m['no_bid_dollars'])) * 100, 1)
            except (ValueError, TypeError):
                m['yes_ask'] = 0
    result = jsonify({"markets": markets})
    result.headers['Cache-Control'] = 'public, max-age=3600'
    return result


def _require_admin_token(f):
    """Decorator that rejects requests without a valid ADMIN_API_TOKEN."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not ADMIN_API_TOKEN:
            return jsonify({'error': 'Admin API is disabled (ADMIN_API_TOKEN not configured)'}), 503
        token = request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
        if not token or not hmac.compare_digest(token, ADMIN_API_TOKEN):
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


@app.route('/api/admin/repair-snapshots', methods=['POST'])
@_require_admin_token
def repair_snapshots_admin():
    """Run JSONL repair on demand and return recovery metadata."""
    try:
        stats = repair_snapshots_jsonl(HISTORICAL_DATA_PATH)
        return jsonify({
            'success': True,
            'stats': stats,
            'historical_data_path': HISTORICAL_DATA_PATH
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/recover-snapshots', methods=['POST'])
@_require_admin_token
def recover_snapshots_admin():
    """Rebuild snapshots by stitching a CSV history source with current volume JSONL data."""
    try:
        csv_path = REPO_CSV_PATH
        bridge_interval_minutes = int(request.args.get('bridge_minutes', 3))
        max_bridge_hours = int(request.args.get('max_bridge_hours', 72))
        apply_changes = (request.args.get('apply', '0').strip().lower() in {'1', 'true', 'yes'})
        csv_only = (request.args.get('csv_only', '0').strip().lower() in {'1', 'true', 'yes'})

        stats = recover_snapshots_from_csv_and_current(
            csv_path=csv_path,
            current_path=HISTORICAL_DATA_PATH,
            output_path=HISTORICAL_DATA_PATH,
            bridge_interval_minutes=bridge_interval_minutes,
            max_bridge_hours=max_bridge_hours,
            dry_run=not apply_changes,
            csv_only=csv_only
        )
        # Optionally bridge to present after recovery
        bridge_stats = None
        if apply_changes and request.args.get('bridge', '0').strip().lower() in {'1', 'true', 'yes'}:
            bridge_stats = bridge_to_present(HISTORICAL_DATA_PATH)

        return jsonify({
            'success': True,
            'csv_path': csv_path,
            'historical_data_path': HISTORICAL_DATA_PATH,
            'applied': apply_changes,
            'csv_only': csv_only,
            'stats': stats,
            'bridge_to_present': bridge_stats
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/bridge-to-present', methods=['POST'])
@_require_admin_token
def bridge_to_present_admin():
    """Fill the gap from the last snapshot to now with flat-interpolated data."""
    try:
        stats = bridge_to_present(HISTORICAL_DATA_PATH)
        return jsonify({
            'success': True,
            'historical_data_path': HISTORICAL_DATA_PATH,
            'stats': stats
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/force-csv-recovery', methods=['POST'])
@_require_admin_token
def force_csv_recovery_admin():
    """Force a full CSV recovery regardless of marker state or snapshot counts.
    Directly calls recover function to merge CSV + current JSONL, bypassing all guards."""
    try:
        marker = os.path.join(os.path.dirname(HISTORICAL_DATA_PATH), '.csv_recovery_done')
        if os.path.exists(marker):
            os.remove(marker)
            print(f"[{datetime.now().isoformat()}] Removed recovery marker for forced recovery")

        csv_path = REPO_CSV_PATH
        if not os.path.exists(csv_path):
            return jsonify({'error': f'CSV not found at {csv_path}'}), 404

        # Directly call recover — bypasses count check in import_repo_csv_to_volume_if_needed
        stats = recover_snapshots_from_csv_and_current(
            csv_path=csv_path,
            current_path=HISTORICAL_DATA_PATH,
            output_path=HISTORICAL_DATA_PATH,
            bridge_interval_minutes=3,
            max_bridge_hours=72,
            dry_run=False,
            csv_only=not os.path.exists(HISTORICAL_DATA_PATH)
        )
        stats['reason'] = 'forced_full_merge'

        # Bridge to present if needed
        bridge_stats = bridge_to_present(HISTORICAL_DATA_PATH)
        stats['bridge_to_present'] = bridge_stats

        # Re-create marker
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, 'w') as f:
            f.write(f"force-recovered at {datetime.now(timezone.utc).isoformat()}\n")

        final_count = count_snapshots_jsonl(HISTORICAL_DATA_PATH)
        stats['final_snapshot_count'] = final_count

        return jsonify({
            'success': True,
            'historical_data_path': HISTORICAL_DATA_PATH,
            'stats': stats
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/send-csv-backup', methods=['POST'])
@_require_admin_token
def send_csv_backup_admin():
    """Manually trigger a CSV backup email."""
    try:
        success = send_csv_backup_email()
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/fix-kalshi-gap', methods=['POST'])
@_require_admin_token
def fix_kalshi_gap():
    """One-time fix: remove last N min of Manifold-only data. Default 50 min."""
    try:
        minutes = int(request.args.get('minutes', 50))
        lock_path = HISTORICAL_DATA_PATH + '.lock'
        lock_file = _acquire_file_lock(lock_path)
        try:
            snapshots = read_snapshots_jsonl(HISTORICAL_DATA_PATH)
            if not snapshots:
                return jsonify({"error": "no snapshots"}), 400

            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(minutes=minutes)

            good = []
            removed = 0
            for s in snapshots:
                ts = parse_snapshot_timestamp(s.get('timestamp', ''))
                if ts and ts < cutoff:
                    good.append(s)
                else:
                    removed += 1

            backup_file(HISTORICAL_DATA_PATH, reason='fix-kalshi-gap')

            temp_path = HISTORICAL_DATA_PATH + '.fix_tmp'
            with open(temp_path, 'w') as f:
                for s in good:
                    f.write(json.dumps(s) + '\n')
            os.replace(temp_path, HISTORICAL_DATA_PATH)
        finally:
            _release_file_lock(lock_file)

        return jsonify({
            "success": True,
            "removed": removed,
            "kept": len(good),
            "cutoff_minutes": minutes
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/snapshots/count')
def get_snapshot_count():
    """Return total snapshot count and data points without loading all data"""
    try:
        snapshot_count = count_snapshots_jsonl(HISTORICAL_DATA_PATH)
        data_points = count_data_points_jsonl(HISTORICAL_DATA_PATH)
        return jsonify({
            "count": snapshot_count,
            "snapshots": snapshot_count,
            "data_points": data_points
        })
    except Exception as e:
        return jsonify({"count": 0, "snapshots": 0, "data_points": 0})

@app.route('/api/snapshots')
def get_snapshots():
    """Retrieve historical snapshots for charting (reads JSONL format)"""
    try:
        snapshots = read_snapshots_jsonl(HISTORICAL_DATA_PATH)
        return jsonify(snapshots)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def _compute_chart_data(period, epsilon, raw_lines=None):
    """
    Compute RDP-simplified chart data for a given period and epsilon.
    Returns dict with 'snapshots', 'gaps', 'interpolated_ranges'.
    Pure computation — no Flask request/response handling.
    If raw_lines is provided (list of JSON strings), parses from those
    instead of reading from disk. Each parse yields fresh dicts so EMA
    mutation is safe without deepcopy.
    """
    # Performance strategy by period:
    #   1d: parse only last ~1200 raw lines (tail of chronological JSONL)
    #   7d: parse only last ~8000 raw lines
    #   all: downsample raw lines to ~5000 BEFORE JSON parsing (saves ~300ms),
    #        then detect gaps via fast regex on full set (only ~22ms)
    import re
    MAX_POINTS = 5000
    GAP_THRESHOLD_SECS = 7200  # 2 hours
    _ts_re = re.compile(r'"timestamp":\s*"([^"]+)"')

    # Archive mode: anchor period windows to the most recent snapshot, not to
    # wall-clock "now". Otherwise post-election visits see empty 1d/7d charts.
    if period == '1d':
        max_lines = 1200
        period_days = 1
    elif period == '7d':
        max_lines = 8000
        period_days = 7
    else:
        max_lines = None
        period_days = None

    cutoff = None  # filled in after we know the latest snapshot timestamp below

    # --- Resolve source lines ---
    if raw_lines is not None:
        source_lines = raw_lines
    else:
        source_lines = get_jsonl_raw_lines()
        if not source_lines:
            return {'snapshots': [], 'gaps': [], 'interpolated_ranges': []}

    if not source_lines:
        return {'snapshots': [], 'gaps': [], 'interpolated_ranges': []}

    # --- Anchor period cutoff to the latest snapshot timestamp ---
    # Scan back from the end to find the most recent parseable timestamp.
    if period_days is not None:
        latest_dt = None
        for line in reversed(source_lines):
            m = _ts_re.search(line)
            if m:
                latest_dt = parse_snapshot_timestamp(m.group(1))
                if latest_dt:
                    break
        if latest_dt is not None:
            cutoff = latest_dt - timedelta(days=period_days)

    # --- Gap detection on FULL data via fast regex (no JSON parse needed) ---
    gaps = []
    if period == 'all':
        prev_dt = None
        for line in source_lines:
            m = _ts_re.search(line)
            if m:
                dt = parse_snapshot_timestamp(m.group(1))
                if dt and prev_dt:
                    if (dt - prev_dt).total_seconds() > GAP_THRESHOLD_SECS:
                        gaps.append({
                            'start': prev_dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                            'end': dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                        })
                if dt:
                    prev_dt = dt

    # --- Trim/downsample raw lines BEFORE JSON parsing ---
    if cutoff is not None:
        # 1d/7d: binary-search-ish scan from the end using fast regex to find
        # the earliest line within the cutoff window. This gives the *full*
        # time window regardless of how dense the trailing data is.
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
        lines_to_parse = source_lines[start_idx:]
        # Safety cap: if the window is huge (e.g. dense election-night data),
        # uniformly downsample before parsing.
        if max_lines and len(lines_to_parse) > max_lines:
            step = max(1, len(lines_to_parse) // max_lines)
            lines_to_parse = [lines_to_parse[i] for i in range(0, len(lines_to_parse), step)]
            # Always include the final line so the chart's right edge is correct.
            if lines_to_parse[-1] is not source_lines[-1]:
                lines_to_parse.append(source_lines[-1])
    elif len(source_lines) > MAX_POINTS:
        # all: uniform downsample (keeps first, every Nth, last)
        step = len(source_lines) // MAX_POINTS
        lines_to_parse = [source_lines[0]]
        for i in range(step, len(source_lines) - 1, step):
            lines_to_parse.append(source_lines[i])
        lines_to_parse.append(source_lines[-1])
    else:
        lines_to_parse = source_lines

    # --- JSON parse only the selected lines ---
    all_snapshots = []
    for line in lines_to_parse:
        try:
            all_snapshots.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue

    if not all_snapshots:
        return {'snapshots': [], 'gaps': [], 'interpolated_ranges': []}

    # --- Parse timestamps, filter by period cutoff ---
    parsed = []
    for snap in all_snapshots:
        dt = parse_snapshot_timestamp(snap.get('timestamp', ''))
        if dt:
            if cutoff and dt < cutoff:
                continue
            parsed.append((dt, snap))
    parsed.sort(key=lambda x: x[0])

    if not parsed:
        return {'snapshots': [], 'gaps': [], 'interpolated_ranges': []}

    # For 1d/7d, detect gaps on the filtered (smaller) set
    if period != 'all':
        for i in range(1, len(parsed)):
            gap_secs = (parsed[i][0] - parsed[i - 1][0]).total_seconds()
            if gap_secs > GAP_THRESHOLD_SECS:
                gaps.append({
                    'start': parsed[i - 1][0].strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    'end': parsed[i][0].strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                })

    # Normalize time axis to 0-100 for RDP (same scale as probability 0-100)
    t_first = parsed[0][0].timestamp()
    t_last = parsed[-1][0].timestamp()
    t_range = t_last - t_first if t_last != t_first else 1.0

    # Detect contiguous interpolated ranges (snapshots flagged by bridge/recovery)
    interpolated_ranges = []
    interp_start = None
    for i, (dt, snap) in enumerate(parsed):
        is_interp = snap.get('interpolated', False)
        if is_interp and interp_start is None:
            interp_start = dt
        elif not is_interp and interp_start is not None:
            interpolated_ranges.append({
                'start': interp_start.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                'end': parsed[i - 1][0].strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            })
            interp_start = None
    # Close any range that extends to the end
    if interp_start is not None:
        interpolated_ranges.append({
            'start': interp_start.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            'end': parsed[-1][0].strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        })

    # ===== EMA SMOOTHING PASS =====
    # Apply exponential moving average per candidate to eliminate jitter.
    # alpha controls responsiveness: lower = smoother (0.15 is very smooth)
    EMA_ALPHA = 0.15

    all_candidates = set()
    for _, snap in parsed:
        for c in snap.get('candidates', []):
            cand_name = c.get('name')
            if cand_name:
                all_candidates.add(cand_name)

    # Track EMA state per candidate
    ema_state = {}  # candidate_name -> current smoothed value

    for i, (dt, snap) in enumerate(parsed):
        for c in snap.get('candidates', []):
            name = c.get('name')
            if not name:
                continue
            raw = _safe_float(c.get('probability', 0), 0.0)
            if name not in ema_state:
                ema_state[name] = raw  # First value: no smoothing
            else:
                ema_state[name] = EMA_ALPHA * raw + (1 - EMA_ALPHA) * ema_state[name]
            c['probability'] = round(ema_state[name], 1)

    # ===== RDP SIMPLIFICATION =====
    # Run RDP per candidate on the smoothed data.
    # Scale epsilon by period: 'all' uses larger epsilon for more aggressive
    # simplification (fewer points), '1d' keeps more detail.
    if period == 'all':
        effective_epsilon = max(epsilon, 1.0)
    elif period == '7d':
        effective_epsilon = max(epsilon, 0.5)
    else:
        effective_epsilon = epsilon

    kept_indices = set()
    kept_indices.add(0)
    kept_indices.add(len(parsed) - 1)

    for cand_name in all_candidates:
        # Build polyline for this candidate
        points = []
        index_map = []  # maps polyline index -> parsed index
        for i, (dt, snap) in enumerate(parsed):
            for c in snap.get('candidates', []):
                if c.get('name') == cand_name:
                    x = ((dt.timestamp() - t_first) / t_range) * 100.0
                    y = _safe_float(c.get('probability', 0), 0.0)
                    points.append((x, y))
                    index_map.append(i)
                    break

        if len(points) > 2:
            rdp_indices = rdp_simplify(points, effective_epsilon)
            for ri in rdp_indices:
                kept_indices.add(index_map[ri])

    # ===== ENSURE MINIMUM TIME DENSITY =====
    # Scale density based on period to keep total points reasonable:
    #   1d: ~15 min intervals → ~96 density points max
    #   7d: ~60 min intervals → ~168 density points max
    #   all: scale to target ~300 total points max
    if period == '1d':
        MIN_INTERVAL = 900   # 15 minutes
    elif period == '7d':
        MIN_INTERVAL = 3600  # 1 hour
    else:
        # For 'all', scale interval so we get ~300 points max
        # t_range is in seconds; 300 points → interval = t_range/300
        MIN_INTERVAL = max(3600, int(t_range / 300))

    kept_sorted = sorted(kept_indices)

    additional_indices = set()
    for i in range(len(kept_sorted) - 1):
        idx1 = kept_sorted[i]
        idx2 = kept_sorted[i + 1]
        dt1 = parsed[idx1][0]
        dt2 = parsed[idx2][0]
        time_gap = (dt2 - dt1).total_seconds()

        if time_gap > MIN_INTERVAL:
            num_needed = min(int(time_gap / MIN_INTERVAL), 20)  # cap per-gap additions
            for j in range(1, num_needed + 1):
                target_time = dt1 + timedelta(seconds=j * MIN_INTERVAL)
                # Find closest index to target_time between idx1 and idx2
                for k in range(idx1 + 1, idx2):
                    if parsed[k][0] >= target_time:
                        additional_indices.add(k)
                        break

    kept_indices.update(additional_indices)
    kept_sorted = sorted(kept_indices)
    result_snapshots = []
    for idx in kept_sorted:
        dt, snap = parsed[idx]
        result_snapshots.append(snap)

    return {
        'snapshots': result_snapshots,
        'gaps': gaps,
        'interpolated_ranges': interpolated_ranges
    }


def _prewarm_chart_cache():
    """
    Pre-compute chart data for all periods and store in cache.
    Called after each data collection cycle so user requests always hit cache.
    Runs in a background thread to not block the data collection loop.
    Reads the JSONL file once, then computes all 3 periods from that single read.
    """
    global _chart_cache
    # Determine file size (check .gz first, then plain)
    gz_path = HISTORICAL_DATA_PATH + '.gz'
    try:
        if os.path.exists(gz_path):
            current_file_size = os.path.getsize(gz_path)
        else:
            current_file_size = os.path.getsize(HISTORICAL_DATA_PATH)
    except OSError:
        return

    raw_lines = get_jsonl_raw_lines()
    if not raw_lines:
        return

    now = _time.time()
    for period in ('all', '7d', '1d'):
        cache_key = f'{period}:0.5'
        compute_lock = _get_compute_lock(cache_key)
        with compute_lock:
            # Skip if a user request already computed this while we waited
            with _chart_cache_lock:
                cached = _chart_cache.get(cache_key)
            if cached and cached['file_size'] == current_file_size:
                continue
            try:
                result = _compute_chart_data(period, 0.5, raw_lines=raw_lines)
                etag = hashlib.md5(json.dumps(result, separators=(',', ':')).encode()).hexdigest()[:16]
                with _chart_cache_lock:
                    _chart_cache[cache_key] = {
                        'data': result,
                        'time': now,
                        'file_size': current_file_size,
                        'etag': etag
                    }
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] Error pre-warming cache for {period}: {e}")


@app.route('/api/snapshots/chart')
def get_snapshots_chart():
    """
    Return RDP-simplified snapshots for chart rendering.
    Params:
      period: '1d', '7d', 'all' (default 'all')
      epsilon: RDP tolerance (default 0.5)
    Returns ~200-400 points instead of 5000+ raw.

    Performance: cache is pre-warmed at startup and invalidated when JSONL file size changes.
    Most requests serve from cache with no computation at all.
    Supports ETag/If-None-Match for instant 304 responses on repeat visits.
    """
    try:
        period = request.args.get('period', 'all')
        if period not in ('1d', '7d', 'all'):
            return jsonify({'error': 'Invalid period; use 1d, 7d, or all'}), 400
        try:
            epsilon = float(request.args.get('epsilon', '0.5'))
            epsilon = max(0.1, min(epsilon, 5.0))
        except (TypeError, ValueError):
            return jsonify({'error': 'invalid epsilon'}), 400
        cache_key = f'{period}:{epsilon}'

        now = _time.time()
        gz_path = HISTORICAL_DATA_PATH + '.gz'
        try:
            if os.path.exists(gz_path):
                current_file_size = os.path.getsize(gz_path)
            else:
                current_file_size = os.path.getsize(HISTORICAL_DATA_PATH)
        except OSError:
            current_file_size = 0

        # Check cache: valid if file size unchanged (no new snapshots since last compute)
        with _chart_cache_lock:
            cached = _chart_cache.get(cache_key)

        if cached and cached['file_size'] == current_file_size and cached['data']:
            # ETag: if client already has this version, return 304
            client_etag = request.headers.get('If-None-Match', '').strip('" ')
            if client_etag and client_etag == cached.get('etag'):
                from flask import Response
                return Response(status=304, headers={
                    'ETag': f'"{cached["etag"]}"',
                    'Cache-Control': 'public, max-age=120'
                })

            resp = jsonify(cached['data'])
            if cached.get('etag'):
                resp.headers['ETag'] = f'"{cached["etag"]}"'
            resp.headers['Cache-Control'] = 'public, max-age=120'
            return resp

        # Cache miss: acquire per-key lock so only one thread computes.
        # Other threads with the same key wait for the result instead of
        # all computing independently (thundering herd protection).
        compute_lock = _get_compute_lock(cache_key)
        with compute_lock:
            # Re-check cache — another thread may have populated it while we waited
            with _chart_cache_lock:
                cached = _chart_cache.get(cache_key)
            if cached and cached['file_size'] == current_file_size and cached['data']:
                resp = jsonify(cached['data'])
                if cached.get('etag'):
                    resp.headers['ETag'] = f'"{cached["etag"]}"'
                resp.headers['Cache-Control'] = 'public, max-age=120'
                return resp

            # Actually compute
            result = _compute_chart_data(period, epsilon)
            etag = hashlib.md5(json.dumps(result, separators=(',', ':')).encode()).hexdigest()[:16]

            with _chart_cache_lock:
                _chart_cache[cache_key] = {
                    'data': result,
                    'time': now,
                    'file_size': current_file_size,
                    'etag': etag
                }

        resp = jsonify(result)
        resp.headers['ETag'] = f'"{etag}"'
        resp.headers['Cache-Control'] = 'public, max-age=120'
        return resp

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/fec/candidates')
def get_fec_candidates():
    """
    Fetch FEC campaign finance data for all IL-09 2026 candidates.
    Returns comprehensive financial metrics including burn rates.
    """
    try:
        data = fetch_all_fec_data()

        if not data:
            # Return placeholder data structure if FEC data not yet available
            return jsonify({
                "available": False,
                "message": "FEC data will be available after the January 31st filing deadline",
                "candidates": []
            })

        return jsonify({
            "available": True,
            "updated": datetime.now(timezone.utc).isoformat(),
            "candidates": data
        })
    except Exception as e:
        return jsonify({"error": str(e), "available": False}), 500


@app.route('/api/download/snapshots')
def download_snapshots():
    """Download all historical snapshot data as JSONL file"""
    try:
        gz_path = HISTORICAL_DATA_PATH + '.gz'
        if os.path.exists(gz_path):
            # Decompress and serve as plain JSONL for user convenience
            import io
            with gzip.open(gz_path, 'rb') as gz_f:
                data = gz_f.read()
            return send_file(
                io.BytesIO(data),
                mimetype='application/x-ndjson',
                as_attachment=True,
                download_name='il9cast_historical_data.jsonl'
            )
        elif os.path.exists(HISTORICAL_DATA_PATH):
            return send_file(
                HISTORICAL_DATA_PATH,
                mimetype='application/x-ndjson',
                as_attachment=True,
                download_name='il9cast_historical_data.jsonl'
            )
        else:
            return jsonify({"error": "No data available"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/download/snapshots/csv')
def download_snapshots_csv():
    """Download all historical snapshot data as CSV file"""
    try:
        snapshots = read_snapshots_jsonl(HISTORICAL_DATA_PATH)
        if not snapshots:
            return jsonify({"error": "No data available"}), 404

        # Build CSV content
        import io
        import csv
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['timestamp', 'candidate', 'probability', 'hasKalshi', 'interpolated'])

        for snapshot in snapshots:
            timestamp = snapshot.get('timestamp', '')
            is_interpolated = 'true' if snapshot.get('interpolated', False) else 'false'
            for candidate in snapshot.get('candidates', []):
                name = candidate.get('name', '')
                prob = _safe_float(candidate.get('probability', 0), 0.0)
                has_kalshi = 'true' if candidate.get('hasKalshi', False) else 'false'
                writer.writerow([timestamp, name, f'{prob:.1f}', has_kalshi, is_interpolated])

        csv_content = output.getvalue()
        output.close()

        # Create response
        from flask import Response
        response = Response(csv_content, mimetype='text/csv')
        response.headers['Content-Disposition'] = 'attachment; filename=il9cast_historical_data.csv'
        return response

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/subscribe', methods=['POST'])
def subscribe():
    """Archive mode: subscriptions are closed."""
    return jsonify({'error': 'Email alerts ended with the March 17, 2026 primary.'}), 410

@app.route('/unsubscribe')
def unsubscribe():
    """Unsubscribe via signed token link."""
    email = request.args.get('email', '').lower().strip()
    token = request.args.get('token', '')

    if not email or not token:
        return render_template('unsubscribe.html', success=False, message='Invalid unsubscribe link.')

    if not verify_unsub_token(email, token):
        return render_template('unsubscribe.html', success=False, message='Invalid unsubscribe link.')

    removed = remove_subscriber(email)
    if removed:
        return render_template('unsubscribe.html', success=True, message=f'{email} has been unsubscribed.')
    else:
        return render_template('unsubscribe.html', success=True, message='You are already unsubscribed.')

@app.route('/api/test-swing-alert')
@_require_admin_token
def test_swing_alert():
    """Test endpoint: send a fake swing alert to all subscribers (admin only)."""
    fake_swings = [
        {'name': 'Daniel Biss', 'old': 58.2, 'new': 64.7, 'delta': 6.5},
        {'name': 'Jan Schakowsky', 'old': 24.1, 'new': 18.3, 'delta': -5.8}
    ]

    subscribers = read_subscribers()
    count = 0
    for sub in subscribers:
        threshold = sub.get('threshold', 5.0)
        # Filter swings that meet this subscriber's threshold
        subscriber_swings = [s for s in fake_swings if abs(s['delta']) >= threshold]
        if subscriber_swings:
            send_swing_alert_to_subscriber(sub['email'], subscriber_swings)
            count += 1

    return jsonify({'success': True, 'message': f'Test swing alert sent to {count} subscriber(s)'})

@app.route('/api/broadcast', methods=['POST'])
@_require_admin_token
def broadcast_email():
    """Send a one-time broadcast email to all subscribers. Requires admin token."""

    subscribers = read_subscribers()
    if not subscribers:
        return jsonify({'success': True, 'message': 'No subscribers'})

    count = 0
    for sub in subscribers:
        email = sub['email']
        token = make_unsub_token(email)
        unsub_url = f"{SITE_BASE_URL}unsubscribe?email={email}&token={token}"

        text = """
Thanks for subscribing to IL9Cast!

We're working on some exciting new features, including the possibility of building a precinct-by-precinct model for the IL-9 primary.

Stay tuned - more updates coming soon.

View Live Markets: """ + SITE_BASE_URL + """markets
"""

        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
        <body style="margin: 0; padding: 0; background-color: #1A1A1E; font-family: 'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #1A1A1E;">
                <tr><td align="center" style="padding: 40px 20px;">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width: 600px; background-color: #232328; border: 1px solid #31B0B5;">
                        <!-- Logo -->
                        <tr><td style="padding: 32px 40px 0 40px; text-align: center; border-bottom: 1px solid #2a2a30;">
                            <h1 style="margin: 0 0 6px 0; font-family: Georgia, 'Times New Roman', serif; font-size: 28px; font-weight: 400; letter-spacing: 1px;">
                                <span style="color: #F0EFEB;">IL9</span><span style="color: #31B0B5;">Cast</span>
                            </h1>
                            <p style="margin: 0 0 20px 0; color: #888; font-size: 11px; letter-spacing: 2px; text-transform: uppercase;">A Quick Update</p>
                        </td></tr>

                        <!-- Message -->
                        <tr><td style="padding: 32px 40px;">
                            <p style="margin: 0 0 16px 0; color: #F0EFEB; font-size: 16px; line-height: 1.7;">Thanks for subscribing to IL9Cast.</p>
                            <p style="margin: 0 0 16px 0; color: #ccc; font-size: 15px; line-height: 1.7;">We're working on some new things behind the scenes, including the possibility of building a <strong style="color: #31B0B5;">precinct-by-precinct model</strong> for the IL-9 primary.</p>
                            <p style="margin: 0; color: #ccc; font-size: 15px; line-height: 1.7;">Stay tuned &mdash; more updates coming soon.</p>
                        </td></tr>

                        <!-- CTA -->
                        <tr><td style="padding: 0 40px 32px 40px; text-align: center;">
                            <a href="{SITE_BASE_URL}markets" style="display: inline-block; background-color: #31B0B5; color: #ffffff; text-decoration: none; padding: 12px 32px; font-weight: 600; font-size: 15px;">View Live Markets</a>
                        </td></tr>

                        <!-- Footer -->
                        <tr><td style="padding: 20px 40px; text-align: center; border-top: 1px solid #2a2a30;">
                            <p style="margin: 0; color: #555; font-size: 11px;"><a href="{unsub_url}" style="color: #555; text-decoration: underline;">Unsubscribe</a></p>
                        </td></tr>
                    </table>
                </td></tr>
            </table>
        </body>
        </html>
        """
        if send_email(email, 'IL9Cast - New Things Coming', html, text):
            count += 1

    return jsonify({'success': True, 'message': f'Broadcast sent to {count} subscriber(s)'})


# Prediction-market data collection ran every 3 minutes from Jan through
# election night (March 17, 2026). The site is now a static archive; the
# scheduler is gone and the JSONL snapshot file is read-only.


# Pre-warm chart cache at startup (archive site: one-time cost, every request hits cache).
# Synchronous prewarm avoids lock contention between background thread and first visitors.
if os.environ.get('IL9_SKIP_STARTUP_TASKS', '').strip().lower() not in ('1', 'true', 'yes'):
    try:
        _prewarm_chart_cache()
    except Exception as _prewarm_err:
        print(f"[{datetime.now().isoformat()}] Chart cache prewarm skipped: {_prewarm_err}")

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

if __name__ == '__main__':
    # Use debug mode only for local development
    import os
    debug_mode = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true')
    port = int(os.environ.get('PORT', 8000))
    app.run(debug=debug_mode, host='0.0.0.0', port=port)
