# Volume Deletion Incident — February 13, 2026

## What Happened

Railway deleted the persistent volume overnight, wiping **20,000+ historical snapshots** down to just 3 post-wipe scraper entries. The existing recovery code (`import_repo_csv_to_volume_if_needed()`) was designed only for fresh deployments — it skipped recovery if the JSONL file had *any* data at all, so the 3 surviving post-wipe snapshots prevented the CSV re-import from ever triggering.

## Critical Design Principle: Never Overwrite Real Data

After the volume wipe (~5:05 GMT), the scraper immediately started collecting **real, live market data** again. When we recover older history from the CSV export, the recovery **must not overwrite** any of that real post-wipe data.

The recovery function (`recover_snapshots_from_csv_and_current()`) enforces this at `app.py:291`:

```python
base = [s for s in csv_snapshots if parse_snapshot_timestamp(s.get('timestamp')) < first_current_dt]
```

1. **CSV data only fills the gap before real data starts.** Any CSV snapshot whose timestamp overlaps with or comes after the first surviving real snapshot is **discarded**.
2. **Interpolated bridge fills the time gap.** Between the last CSV snapshot and the first real post-wipe snapshot, interpolated data with realistic noise bridges the gap.
3. **Real post-wipe data is appended last, untouched.** Merge order is always: `csv_history + interpolated_bridge + real_surviving_data`.

So if you later update the CSV with more recent data (e.g., data up to Feb 12), recovery will still **only use CSV data for timestamps before the first real snapshot** on the volume. The real data collected live after the wipe always wins.

## What Was Fixed (4 commits, PRs #53 and #54)

### 1. Volume Wipe Recovery

**Problem:** The old recovery check was `if JSONL exists -> skip`. After a volume wipe, Railway's scraper would write a few new snapshots before the next restart, and recovery would never fire.

**Fix:** Marker-file based detection + 3-case recovery logic:

| Scenario | Detection | Action |
|----------|-----------|--------|
| Healthy volume | `.csv_recovery_done` marker exists, or JSONL count >= CSV count | Skip (no-op) |
| Volume wipe with surviving data | JSONL count < CSV count, marker missing | Stitch: CSV history + interpolated bridge + surviving JSONL |
| Fresh/empty volume | No JSONL file at all | Full CSV import + bridge to present |

**New functions:**
- `bridge_to_present()` — fills gap from last snapshot to now with interpolated data at 3-min intervals
- `recover_snapshots_from_csv_and_current()` — rebuilds full timeline merging CSV + bridge + surviving data, with deduplication
- `_acquire_file_lock()` / `_release_file_lock()` — prevents concurrent writes during recovery

**New admin endpoints:**
- `POST /api/admin/bridge-to-present` — manually fill gap to present
- `POST /api/admin/force-csv-recovery` — remove marker + re-import from CSV
- `POST /api/admin/recover-snapshots?bridge=1` — existing endpoint gains bridge option

### 2. Realistic Interpolation with Noise

**Problem:** Bridge data was a flat line (same values repeated every 3 minutes), which looked obviously fake on the chart.

**Fix:** `_interpolate_snapshots()` now accepts `add_noise=True`:
- **Random walk** with mean-reversion around the linear trend line
- **Noise magnitude** scales with candidate probability level
- **Edge dampening** — noise fades to zero near endpoints for smooth connections
- **Seeded RNG** — deterministic output (same gap = same interpolated data every time)
- Every interpolated snapshot flagged with `"interpolated": true`

### 3. Visual Indicator for Estimated Data

**Backend:** `/api/snapshots/chart` scans for contiguous runs of `interpolated: true` snapshots and returns them as `interpolated_ranges`.

**Frontend:** New Chart.js plugin `interpolatedRegionPlugin`:
- Subtle shaded background over interpolated time periods
- Dashed vertical border lines at region edges
- "This data may not be fully accurate" label below the x-axis
- Dark mode aware
- Trend lines remain normal/solid — only background shading signals estimated data

### 4. Gitignore Cleanup

- `data/*.lock` — runtime file locks
- `data/.csv_recovery_done` — recovery marker
- `data/.purge_pre_jan30_done` — purge marker

## Recovery Workflow (for future volume wipes)

**Automatic (on Railway restart):**
1. `import_repo_csv_to_volume_if_needed()` runs on startup
2. Detects missing `.csv_recovery_done` marker or JSONL count < CSV count
3. Imports CSV, bridges gap to present, creates marker
4. Real post-wipe snapshots are **always preserved**

**Manual (with updated CSV):**
1. Replace `il9cast_historical_data.csv` in repo root with newer export
2. Push to main
3. Force recovery: `curl -X POST https://il9.org/api/admin/force-csv-recovery`
4. CSV fills history, bridge fills the gap, real data stays untouched

## Related PRs

- [PR #53](https://github.com/de-bayes/IL9/pull/53) — Volume wipe recovery, bridge interpolation, admin endpoints, marker-based detection
- [PR #54](https://github.com/de-bayes/IL9/pull/54) — Realistic noise for interpolated data, visual chart indicator, gitignore cleanup
