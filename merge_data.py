#!/usr/bin/env python3
"""
merge_data.py - Merge and deduplicate IL-09 historical snapshot data

Reads:
  data/historical_snapshots.jsonl (primary, 5565 snapshots, Jan 24-28)
  ~/Desktop/il9cast_historical_data.jsonl (desktop master, 486 snapshots, Jan 22-30)

Writes:
  data/historical_snapshots.jsonl (merged, deduplicated)
  data/historical_snapshots.jsonl.pre-merge-backup.YYYYMMDD_HHMMSS (backup)
"""
import json
import os
import shutil
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PRIMARY_PATH = os.path.join(SCRIPT_DIR, 'data', 'historical_snapshots.jsonl')
DESKTOP_PATH = os.path.expanduser('~/Desktop/il9cast_historical_data.jsonl')
BACKUP_SUFFIX = '.pre-merge-backup.' + datetime.now().strftime('%Y%m%d_%H%M%S')


def parse_timestamp_to_utc(ts_str):
    """Parse timestamp string to UTC datetime.
    All timestamps are UTC; some early entries just lack the Z suffix.
    """
    if not ts_str:
        return None
    ts_clean = ts_str.rstrip('Z')
    for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']:
        try:
            dt = datetime.strptime(ts_clean, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def read_jsonl(filepath):
    """Read JSONL file, return list of dicts."""
    snapshots = []
    if not os.path.exists(filepath):
        print(f"  File not found: {filepath}")
        return snapshots
    with open(filepath, 'r') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                snapshots.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  Skipping line {line_num}: {e}")
    return snapshots


def normalize_timestamp(snapshot):
    """Parse timestamp, normalize to UTC Z-suffix format."""
    ts = snapshot.get('timestamp', '')
    dt = parse_timestamp_to_utc(ts)
    if dt:
        snapshot['_utc_dt'] = dt
        snapshot['timestamp'] = dt.strftime('%Y-%m-%dT%H:%M:%S.') + f'{dt.microsecond:06d}' + 'Z'
    return snapshot


def deduplicate(snapshots, threshold_seconds=1.0):
    """Remove snapshots within threshold_seconds of each other, keeping the first."""
    if not snapshots:
        return []

    sorted_snaps = sorted(snapshots, key=lambda s: s['_utc_dt'])

    kept = [sorted_snaps[0]]
    for snap in sorted_snaps[1:]:
        diff = (snap['_utc_dt'] - kept[-1]['_utc_dt']).total_seconds()
        if diff >= threshold_seconds:
            kept.append(snap)

    return kept


def main():
    print("=" * 60)
    print("IL-09 Data Merge Script")
    print("=" * 60)

    # 1. Read both files
    print(f"\nReading primary: {PRIMARY_PATH}")
    primary = read_jsonl(PRIMARY_PATH)
    print(f"  -> {len(primary)} snapshots")

    print(f"\nReading desktop master: {DESKTOP_PATH}")
    desktop = read_jsonl(DESKTOP_PATH)
    print(f"  -> {len(desktop)} snapshots")

    # 2. Normalize all timestamps to UTC
    all_snapshots = []
    skipped = 0
    for s in primary + desktop:
        normalized = normalize_timestamp(s)
        if normalized.get('_utc_dt'):
            all_snapshots.append(normalized)
        else:
            skipped += 1

    print(f"\nCombined: {len(all_snapshots)} snapshots ({skipped} skipped due to bad timestamps)")

    # 3. Date range before dedup
    all_snapshots.sort(key=lambda s: s['_utc_dt'])
    if all_snapshots:
        first = all_snapshots[0]['_utc_dt']
        last = all_snapshots[-1]['_utc_dt']
        print(f"Date range: {first.isoformat()} -> {last.isoformat()}")

    # 4. Deduplicate (1-second threshold)
    deduped = deduplicate(all_snapshots, threshold_seconds=1.0)
    removed = len(all_snapshots) - len(deduped)
    print(f"\nAfter dedup: {len(deduped)} snapshots ({removed} duplicates removed)")

    # 5. Detect gaps > 1 hour
    print("\nGaps > 1 hour:")
    gap_count = 0
    for i in range(1, len(deduped)):
        gap = (deduped[i]['_utc_dt'] - deduped[i - 1]['_utc_dt']).total_seconds()
        if gap > 3600:
            gap_hours = gap / 3600
            t1 = deduped[i - 1]['_utc_dt'].isoformat()
            t2 = deduped[i]['_utc_dt'].isoformat()
            print(f"  {t1} -> {t2} ({gap_hours:.1f} hours)")
            gap_count += 1
    if gap_count == 0:
        print("  None found")

    # 6. Remove internal _utc_dt field
    for s in deduped:
        del s['_utc_dt']

    # 7. Backup original
    if os.path.exists(PRIMARY_PATH):
        backup_path = PRIMARY_PATH + BACKUP_SUFFIX
        shutil.copy2(PRIMARY_PATH, backup_path)
        print(f"\nBackup created: {backup_path}")

    # 8. Write merged result
    with open(PRIMARY_PATH, 'w') as f:
        for s in deduped:
            f.write(json.dumps(s) + '\n')

    file_size = os.path.getsize(PRIMARY_PATH)
    print(f"\nWritten: {PRIMARY_PATH}")
    print(f"  {len(deduped)} snapshots, {file_size / 1024:.1f} KB")
    print("\nDone!")


if __name__ == '__main__':
    main()
