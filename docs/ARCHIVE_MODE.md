# Archive mode (current)

IL9Cast is a **static archive** of the IL-9 Democratic primary prediction markets and related models. The primary was **March 17, 2026**. Live data collection has stopped.

## What still runs

- Flask app serving pages, charts, downloads, and the precinct model
- **Chart API** — `GET /api/snapshots/chart` (RDP + EMA; periods anchored to last snapshot, not wall-clock “now”)
- **Archived market JSON** — `GET /api/manifold`, `GET /api/kalshi` read from `data/archive/*.json` (no live upstream calls)
- **Precinct GeoJSON** — `GET /api/model/precincts` (gzip when supported)
- **Admin endpoints** — JSONL repair/recovery (token required); use with care on production volume

## What does not run

- 3-minute Manifold/Kalshi scraper (removed; see comment at bottom of `app.py`)
- `POST /api/snapshot` (removed; do not call from the browser)
- Email swing alerts / daily summary scheduler
- `POST /api/subscribe` — returns **410** (alerts ended)

## Data

- Historical snapshots: `data/historical_snapshots.jsonl` or `.jsonl.gz` on Railway volume `/app/data`
- Recovery marker: `data/.csv_recovery_done` (gitignored; recreated on healthy volume)
- Repo bootstrap CSV: `il9cast_historical_data.csv`

Recovery import runs at startup unless skipped (`IL9_SKIP_STARTUP_TASKS=1`). By default **no bridge-to-present** after recovery (`ENABLE_RECOVERY_BRIDGE` unset) so post-election restarts do not append synthetic snapshots.

## Frontend

- **Markets** — reads archived APIs + chart snapshots; no client-side snapshot POSTs
- **Archive banner** — `.archive-banner` on landing and markets pages
- **Model** — Leaflet map; prefetch `/api/model/precincts`

## Related

- [DEPLOYMENT.md](DEPLOYMENT.md) — how to run on Railway
- [DATA_PROTECTION_PROMPT.md](DATA_PROTECTION_PROMPT.md) — recovery rules
