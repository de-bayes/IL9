"""Auto-extracted from app.py — IL9Cast service module."""

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

