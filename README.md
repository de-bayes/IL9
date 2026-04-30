# IL9Cast - Illinois 9th District Primary Forecast

A prediction-market and modeling site for the Illinois 9th Congressional District Democratic Primary (**March 17, 2026**). The live scraper has been retired; the deployment is an **archive** of historical JSONL snapshots, archived Manifold/Kalshi API payloads, the precinct model, and campaign-finance views.

## Features

- **Historical markets** — Chart and bar breakdown from stored snapshots and `data/archive/` API snapshots (no live Manifold/Kalshi calls from the server)
- **Smoothing stack** — EMA (alpha 0.15), Ramer–Douglas–Peucker simplification, monotone cubic interpolation on the chart; **2-hour** gap threshold for dashed segments
- **Thin-market handling** — Kalshi midpoint/liquidity-style components fall back to last price when there is no yes-side bid (`yes_bid = 0`); see Markets page JavaScript
- **Central Time** — Display via `America/Chicago` in the browser
- **Data download** — `GET /api/download/snapshots` (JSONL; gzip if stored as `.gz`) and CSV export

## Quick Start

### Development

```bash
git clone https://github.com/de-bayes/IL9.git
cd IL9

pip install -r requirements.txt

python app.py
# Default: http://localhost:8000  (set PORT to override)
```

### Production (Railway)

```bash
git push origin main
# or: railway up
```

`railway.toml` starts with `gunicorn app:app --preload`.

## Architecture Overview

### Backend (Flask)

- **Framework:** Flask 2.3.2, Gunicorn in production  
- **Data:** JSONL timeline (`historical_snapshots.jsonl` or `.jsonl.gz`); path resolution in `resolve_data_path()`  
- **Chart API:** `/api/snapshots/chart` — EMA, RDP, gap detection (`GAP_THRESHOLD_SECS = 7200`)

### Frontend

- **Markets:** Chart.js, archived APIs under `/api/manifold` and `/api/kalshi`  
- **Model:** Leaflet + precinct GeoJSON on `/odds`  
- **Timezone:** `Intl.DateTimeFormat` with `America/Chicago`

### How snapshots were produced (historical)

Through election night, snapshots were appended every **3 minutes** using a **40% / 42% / 12% / 6%** Manifold/Kalshi blend, soft normalization, and ±3% spike dampening vs. the previous snapshot. That writer is no longer part of the running app.

## Key Technical Details

### Data format

Each JSONL line resembles:

```json
{"candidates": [{"name": "Daniel Biss", "probability": 63.6, "hasKalshi": true}], "timestamp": "2026-01-30T19:45:30Z"}
```

### APIs (examples)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/manifold` | Archived Manifold JSON (`data/archive/manifold.json`) |
| GET | `/api/kalshi` | Archived Kalshi markets JSON (`data/archive/kalshi.json`) |
| GET | `/api/snapshots` | Full snapshot array |
| GET | `/api/snapshots/chart?period=…` | Chart-ready series (`period` = `1d`, `7d`, or `all`; EMA + RDP) |
| GET | `/api/snapshots/count` | Snapshot count |
| GET | `/api/download/snapshots` | Download JSONL |

There is **no** `POST /api/snapshot` route in `app.py` (the Markets template still references it; safe to ignore or remove later).

## Deployment (Railway)

- **Builder:** NIXPACKS  
- **Start:** `gunicorn app:app --preload`  
- **Health check:** `GET /`  
- **Volume:** `/app/data` for persistent JSONL when used  

## Dependencies

See `requirements.txt`: Flask, Werkzeug, requests, gunicorn.

## Technical deep dive

See [CLAUDE.md](./CLAUDE.md) for file layout, recovery/admin routes, and accuracy notes.

## About

IL9Cast was created by Ryan McComb. Repository: [github.com/de-bayes/IL9](https://github.com/de-bayes/IL9). Site: [il9cast.com](https://il9cast.com).

## License

Open source when a LICENSE file is present in the repository.

---

**Documentation refresh:** April 2026 (aligned with post-primary archive codebase).
