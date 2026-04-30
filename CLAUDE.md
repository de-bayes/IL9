# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**IL9Cast** — Illinois 9th District Democratic Primary (March 17, 2026) forecast site: prediction-market history, precinct model, and campaign-finance views. **As of post-primary archive mode**, the live scraper is removed; the app serves **read-only** historical JSONL (optionally gzip-compressed), archived Manifold/Kalshi API responses from `data/archive/`, and precomputed chart data (EMA + RDP smoothing, gap detection). Timestamps on the Markets page use **Central Time** in the browser.

**Historical note:** Prediction-market data was collected every **3 minutes** from late January through election night using a weighted Manifold/Kalshi formula; that pipeline is no longer in `app.py` (see comment near the bottom of `app.py`).

## Development Commands

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Run development server (with auto-reload)
python app.py

# Run production server locally (matches Railway start command)
gunicorn app:app --preload
```

Default dev port is **8000** (`PORT` env overrides).

### Data Management
```bash
# View recent snapshots (JSONL format; use zcat if only .gz exists)
tail -n 50 data/historical_snapshots.jsonl

# Check data file size
ls -lh data/historical_snapshots.jsonl*

# Count total snapshots (without loading all into memory)
wc -l data/historical_snapshots.jsonl
# Or from Python:
python -c "from app import count_snapshots_jsonl, HISTORICAL_DATA_PATH; print(count_snapshots_jsonl(HISTORICAL_DATA_PATH))"
```

### Deployment
```bash
# Railway deployment (automatic on git push)
git push origin main

# Manual Railway CLI deploy
railway up

# View Railway logs
railway logs
```

## Architecture

### Application Structure

**Backend:** Flask 2.3.2 (`app.py`, ~2,700 lines) — routes, snapshot/chart APIs, recovery/admin tools, email helpers, FEC JSON, precinct/candidate pages.

- **No background market scraper** in the current tree; chart data is computed from stored JSONL on request (with in-memory cache keyed by file size).
- **Production:** `railway.toml` uses `gunicorn app:app --preload`. Root `Procfile` is `gunicorn app:app` without `--preload` (prefer `railway.toml` for deploy).

**Frontend:** Jinja2 templates + Chart.js + Leaflet where applicable  
- `templates/landing_new.html` — Homepage  
- `templates/markets.html` — Markets chart and archived live bar breakdown  
- `templates/methodology.html` — Methodology foldouts  
- `templates/odds.html` — Precinct model (Monte Carlo, map, tables)  
- `templates/fundraising.html` / money pages — Campaign finance views  
- `static/style.css` — Shared styling (large file)

### Historical Data Collection Pipeline (how JSONL was built)

Through election night 2026, snapshots were produced on a **3-minute** cadence. Conceptually each cycle:

1. Fetch Manifold (`/v0/slug/who-will-win-the-democratic-primary-RZdcps6dL9`)  
2. Fetch Kalshi (`/trade-api/v2/markets?series_ticker=KXIL9D&status=open`)  
3. Normalize candidate names across platforms  
4. **Weighted aggregate:** Manifold 40%; Kalshi last 42%; midpoint 12%; liquidity-style 6% (midpoint/liquidity fall back to `last_price` when `yes_bid = 0`)  
5. **Soft normalize** (~30% strength toward sum 100%)  
6. **Spike dampen** ±3% per candidate vs. previous snapshot  
7. UTC timestamp; append to JSONL  

If **both** APIs failed, that interval was skipped.

### Thin-Market Fallback

When `yes_bid = 0`, midpoint and liquidity-style components must not use `(0 + yes_ask) / 2`; they fall back to **last price**. The live bar math on the Markets page implements this in `templates/markets.html` (Kalshi breakdown).

### Data Storage

**Snapshots:** `historical_snapshots.jsonl` (JSON Lines). Resolved by `resolve_data_path()` — checks `DATA_DIR`, then looks for an existing file under `/app/data`, `/data`, or repo `data/` (supports plain or `.gz`).

- **Append path in code:** `append_snapshot_jsonl()` uses a **file lock**, append mode, and `fsync` (not the temp-file full rewrite described in older docs).  
- **Optional gzip:** If `historical_snapshots.jsonl.gz` exists, readers prefer it.

**Optional purge:** `purge_old_data()` runs only when `ENABLE_PRE_JAN30_PURGE` is set; it is **off** by default.

### Chart Smoothing Pipeline (server + browser)

1. **Historical:** Spike dampening at collection time (no longer active; data in JSONL already reflects it).  
2. **EMA** — `alpha=0.15` in `_compute_chart_data` / chart pipeline.  
3. **RDP** — `rdp_simplify()`; query param `epsilon` (default 0.5); `period=all` uses a larger effective epsilon.  
4. **Gap detection** — `GAP_THRESHOLD_SECS = 7200` (2 hours) for dashed segments.  
5. **Frontend** — Chart.js `cubicInterpolationMode: 'monotone'`, `tension` 0.5 on Markets chart.

### Central Time Display

Markets page uses `Intl.DateTimeFormat` with `timeZone: 'America/Chicago'` (see `templates/markets.html`).

### API Endpoints

**Public JSON**

- `GET /api/manifold` — Serves **archived** JSON from `data/archive/manifold.json` (not a live Manifold proxy).  
- `GET /api/kalshi` — Serves **archived** Kalshi-shaped JSON from `data/archive/kalshi.json`.  
- `GET /api/snapshots` — Full snapshot array from JSONL.  
- `GET /api/snapshots/count` — Counts lines in JSONL.  
- `GET /api/snapshots/chart?period={1d|7d|all}&epsilon=0.5` — EMA + RDP + gaps; cache invalidated when snapshot file size changes.  
- `GET /api/download/snapshots` — JSONL download (gzip if stored as `.gz`).  
- `GET /api/download/snapshots/csv` — CSV export.  
- `GET /api/fec/candidates` — Hardcoded FEC summary data.  

**Note:** `templates/markets.html` references `POST /api/snapshot`; there is **no** such route in `app.py` (dead client call).

**Admin / recovery (POST, typically authenticated):** `/api/admin/repair-snapshots`, `/api/admin/recover-snapshots`, `/api/admin/bridge-to-present`, etc.

**Pages (examples):** `/`, `/markets`, `/odds`, `/methodology`, `/about`, `/candidates`, `/money`, `/model/methodology` (methodology PDF).

## Deployment Configuration

**Railway:** NIXPACKS, `gunicorn app:app --preload`, health check `GET /`, volume mount `/app/data`.

**Path resolution:** Prefers `DATA_DIR` if set; else first existing path among `/app/data`, `/data`, and local `data/` for `historical_snapshots.jsonl` (or `.gz`).

### Dependencies (`requirements.txt`)

```
Flask==2.3.2
Werkzeug==2.3.6
requests==2.31.0
gunicorn==21.2.0
```

No APScheduler in current requirements (any scheduler discussion in prose is **historical**).

## UI Reference

### Precinct Model (`/odds`)

Pre-election headline card example: **Biss 77.2%** win probability (100k sims, 436 precincts — see `templates/odds.html`). Post-election copy includes certified vote shares where shown.

### Methodology Page

Four foldouts: markets aggregation math, precinct model, fundraising, infrastructure. Infrastructure section should match **archive** behavior (see `templates/methodology.html`).

## File Structure (key)

- `app.py` — Flask app, JSONL read/chart pipeline, archive API, recovery, routes.  
- `data/archive/manifold.json`, `data/archive/kalshi.json` — Archived API payloads for Markets page.  
- `data/historical_snapshots.jsonl` — Timeline (volume or local; may be gitignored).  
- `railway.toml` — Deploy command and volume.  
- `tests/` — Includes chart/recovery tests.

## Common Tasks

```bash
python app.py
# http://localhost:8000

# Chart API smoke test
curl -s 'http://localhost:8000/api/snapshots/chart?period=7d' | head -c 500
```

## Git Workflow

- `main` — production; push triggers Railway when connected.  
- Update this file when data flow, APIs, or deploy config change materially.
