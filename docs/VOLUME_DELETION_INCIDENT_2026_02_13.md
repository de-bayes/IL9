# Volume Deletion Incident — February 13, 2026

## What Happened

Railway deleted the persistent volume overnight, wiping **20,000+ historical snapshots** down to just 3 post-wipe scraper entries. The existing recovery code (`import_repo_csv_to_volume_if_needed()`) was designed only for fresh deployments — it skipped recovery if the JSONL file had *any* data at all, so the 3 surviving post-wipe snapshots prevented the CSV re-import from ever triggering.

## Critical Design Principle: CSV Is Authoritative for Its Time Range

The recovery function (`recover_snapshots_from_csv_and_current()`) uses a clean merge strategy:

1. **Within the CSV's time range** `[csv_min_dt, csv_max_dt]`: **ONLY CSV data is used.** Any JSONL data in this window (including old bridge/interpolated points) is dropped and replaced by the CSV.
2. **Outside the CSV range:** JSONL data is kept as-is (live post-wipe data).
3. **Gap between CSV end and live data start:** Bridged with interpolated snapshots (realistic noise, flagged `interpolated: true`).

This approach was adopted after discovering that the `interpolated` flag could be lost during successive recovery cycles, making it impossible to identify stale bridge data. The timestamp-range approach requires no flags — the CSV simply wins for its entire range.

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

## Bugs Found During Recovery (PRs #58-59)

### 5. Merge Logic Was Prepend-Only

**Problem:** `recover_snapshots_from_csv_and_current()` only used CSV data from *before* the first JSONL timestamp. After the first recovery, JSONL started at Jan 30 (same as CSV), so updating the CSV with Feb 12 data had zero effect — the new data was silently skipped.

**Fix:** Full merge/dedup. All CSV + JSONL timestamps merged, deduplicated, sorted. `force_csv_recovery_admin()` bypasses the count-based guard that also blocked re-recovery.

### 6. Bridge Data Interleaved with Real Data (Chart Shakiness)

**Problem:** The first recovery created interpolated bridge points for the Feb 11–13 gap. When re-recovering with updated CSV (through Feb 12), bridge points were kept alongside real CSV data — smooth synthetic values alternating with real noisy values every ~1.5 min. EMA (alpha=0.15) couldn't smooth it out.

**Fix:** The `interpolated: true` flag was getting lost during recovery cycles. Instead of relying on flags, the merge now treats the CSV as **authoritative for its entire time range**. Within `[csv_min, csv_max]`, only CSV data is used. All other data in that window is dropped.

### 7. Automated CSV Backup Emails

**Purpose:** Ensure data survives future volume deletions without manual CSV export.

**Implementation:** `send_csv_backup_email()` runs every 4 hours via the background scheduler:
- Builds CSV in-memory from JSONL
- Base64-encodes and attaches to Resend email
- Sends to `rymccomb1@icloud.com` with snapshot count, time range, and file size
- Manual trigger: `POST /api/admin/send-csv-backup`

## Data Protection Layers (as of Feb 14, 2026)

| Layer | What | Frequency | Survives Volume Wipe? |
|-------|------|-----------|----------------------|
| **JSONL on volume** | Primary data store | Every 3 min | No |
| **CSV in git repo** | Manual export in repo root | Manual updates | Yes |
| **Email backup** | CSV attachment via Resend | Every 4 hours | Yes |
| **Auto-recovery** | Import from CSV on startup | On restart | N/A (restores data) |
| **Force-recovery** | Admin endpoint for manual fix | On demand | N/A (restores data) |

## Related PRs

- [PR #53](https://github.com/de-bayes/IL9/pull/53) — Volume wipe recovery, bridge interpolation, admin endpoints, marker-based detection
- [PR #54](https://github.com/de-bayes/IL9/pull/54) — Realistic noise for interpolated data, visual chart indicator, gitignore cleanup
- [PR #58](https://github.com/de-bayes/IL9/pull/58) — Update historical CSV with data through Feb 12
- [PR #59](https://github.com/de-bayes/IL9/pull/59) — Fix merge logic (full dedup, CSV-authoritative, drop bridge data, automated email backups)
