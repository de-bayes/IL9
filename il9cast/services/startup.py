"""Auto-extracted from app.py — IL9Cast service module."""

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

