# Deployment Guide — IL9Cast

Operational notes for deploying this Flask app to **Railway**. The public site is a **post-primary archive**: there is **no** live prediction-market scraper in the current `app.py`. Historical JSONL (and archived API JSON under `data/archive/`) is served read-only.

## Pre-deploy checklist

### 1. Test locally

```bash
pip install -r requirements.txt
python app.py
# http://localhost:8000 (default; override with PORT)
```

Verify `/markets` loads, the chart returns data (`/api/snapshots/chart?period=7d`), and `/api/manifold` and `/api/kalshi` return archived JSON.

### 2. Railway configuration

- **Start command** (authoritative): `railway.toml` → `gunicorn app:app --preload`
- **Volume:** `/app/data` for persistent `historical_snapshots.jsonl` (or `.jsonl.gz`) when you use a volume
- **Health check:** `GET /`

The root `Procfile` contains `gunicorn app:app` without `--preload`; Railway uses `railway.toml` for the deploy start command.

## Deploy

```bash
git push origin main
```

## Verify production

- Open `/markets` — chart and bars should reflect stored data, not live APIs
- `GET /api/snapshots/count` — non-zero when JSONL is present
- `railway logs` — look for errors loading snapshots or templates

## Data on the volume

```bash
railway shell
ls -lh /app/data/historical_snapshots.jsonl*
tail -n 3 /app/data/historical_snapshots.jsonl
```

## Historical context (recovery era)

Older internal notes referred to **60-second** collection and one-off JSON→JSONL migration scripts. The **current** design uses **3-minute** historical snapshots (already in the file), optional gzip, file-lock appends when writing, and admin-only recovery routes — see `CLAUDE.md` and `app.py` for the source of truth.

## Troubleshooting

| Symptom | Likely cause |
|--------|----------------|
| Empty chart | Missing or empty `historical_snapshots.jsonl` at resolved path |
| 500 on `/api/manifold` or `/api/kalshi` | Missing `data/archive/manifold.json` or `kalshi.json` |
| Stale chart after deploy | Chart cache keys off file size; ensure the snapshot file path is correct on the volume |

---

**Last reviewed:** April 2026 (aligned with archive-mode codebase).
