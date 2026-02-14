# Data Protection Prompt for Claude Code Sessions

Paste this into any new Claude Code session working on the IL9Cast codebase.

---

## Prompt

```
CRITICAL DATA PROTECTION RULES FOR IL9CAST

This project stores irreplaceable historical prediction market data in data/historical_snapshots.jsonl.
Railway has deleted the persistent volume before (Feb 13, 2026 incident). Follow these rules strictly:

=== NEVER DO THESE THINGS ===

1. NEVER delete, truncate, or overwrite data/historical_snapshots.jsonl without creating a backup first
2. NEVER run os.remove(), os.unlink(), or shutil.rmtree() on any file in the data/ directory
3. NEVER write code that replaces the JSONL file without using the atomic write pattern (write to .tmp, then os.replace())
4. NEVER modify purge_old_data() to run by default — it must require ENABLE_PRE_JAN30_PURGE=true
5. NEVER remove or weaken the .csv_recovery_done marker file logic
6. NEVER remove the send_csv_backup_email() function or its 4-hour schedule
7. NEVER change import_repo_csv_to_volume_if_needed() to skip recovery when the marker is missing
8. NEVER modify recover_snapshots_from_csv_and_current() to overwrite live data with CSV data outside the CSV's time range
9. NEVER remove admin endpoints: /api/admin/force-csv-recovery, /api/admin/bridge-to-present, /api/admin/send-csv-backup

=== ALWAYS DO THESE THINGS ===

1. ALWAYS use backup_file() before any destructive JSONL operation
2. ALWAYS use file locking (_acquire_file_lock/_release_file_lock) for JSONL writes
3. ALWAYS use the atomic write pattern: write to temp file, then os.replace() into place
4. ALWAYS preserve the CSV recovery system (il9cast_historical_data.csv in repo root)
5. ALWAYS keep the automated email backup (send_csv_backup_email, every 4 hours, to rymccomb1@icloud.com via Resend)
6. ALWAYS test recovery logic changes with dry_run=True before writing
7. ALWAYS ensure the merge logic treats CSV as authoritative within its time range [csv_min_dt, csv_max_dt]
8. ALWAYS keep live/post-recovery data for timestamps outside the CSV range

=== HOW RECOVERY WORKS ===

- On Railway restart: import_repo_csv_to_volume_if_needed() checks for .csv_recovery_done marker
- If marker missing + JSONL has fewer snapshots than CSV → auto-recovery triggers
- recover_snapshots_from_csv_and_current() merges CSV + JSONL:
  - Within CSV time range: CSV data only (authoritative)
  - Outside CSV range: JSONL data only (live data preserved)
  - Gap between CSV end and live data start: bridged with interpolated data
- Manual recovery: POST /api/admin/force-csv-recovery

=== DATA BACKUP LAYERS ===

1. JSONL on Railway volume (primary, lost on volume wipe)
2. CSV in git repo root (manual update, survives wipe)
3. Email backup every 4 hours via Resend (survives wipe)
4. Auto-recovery on startup from CSV (restores after wipe)

=== KEY FILES ===

- app.py: recover_snapshots_from_csv_and_current(), import_repo_csv_to_volume_if_needed(), send_csv_backup_email(), bridge_to_present()
- il9cast_historical_data.csv: authoritative CSV backup in repo root
- data/historical_snapshots.jsonl: live data (on Railway persistent volume)
- data/.csv_recovery_done: marker preventing re-recovery (disappears on volume wipe)
- docs/volume-deletion-incident-2026-02-13.md: full incident report

If you are unsure whether a change could affect data integrity, ASK THE USER FIRST.
```
