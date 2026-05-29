# Deployment (Railway)

## Start command

```
gunicorn app:app --config gunicorn.conf.py
```

Configured in `Procfile` and `railway.toml`. `gunicorn.conf.py` sets:

- `preload_app = True` — chart cache and JSONL line cache warm once in master
- `bind = 0.0.0.0:$PORT`
- `worker_class = gthread` with configurable workers/threads

## Health check

- Path: **`GET /healthz`** (JSON `{"status":"ok"}`)
- Timeout: 100s in `railway.toml` (allows slow cold start if recovery runs)

## Persistent volume

- Mount: `/app/data`
- Stores `historical_snapshots.jsonl` (or `.gz`), subscribers, recovery markers

Optional: `DATA_DIR` env var overrides data directory resolution (see `resolve_data_path()` in `app.py`).

## Environment variables

| Variable | Required | Notes |
|----------|----------|--------|
| `PORT` | Railway sets | Gunicorn bind |
| `ADMIN_API_TOKEN` | Production admin | Admin routes return 503 if unset |
| `EMAIL_SECRET_SALT` | If using email | Unsubscribe tokens; fail closed in prod |
| `RESEND_API_KEY` | Email features | Broadcast / alerts (mostly dormant) |
| `SITE_BASE_URL` | Emails | Default `https://il9.org/` |
| `WEB_CONCURRENCY` | Optional | Gunicorn workers (default CPU-based) |
| `GUNICORN_THREADS` | Optional | Threads per worker (default 4) |
| `IL9_SKIP_STARTUP_TASKS` | Optional | Skip CSV import / purge / repair on import |
| `ENABLE_PRE_JAN30_PURGE` | Optional | One-time purge; default off |
| `ENABLE_RECOVERY_BRIDGE` | Optional | Allow bridge-to-present after CSV recovery |

## Local production test

```bash
pip install -r requirements.txt
gunicorn app:app --config gunicorn.conf.py
python scripts/smoke_test_perf.py
```

## Historical note

`docs/DEPLOYMENT_GUIDE.md` describes the **live** Jan 2026 deployment (60s collection). Do not use it for archive deploys.
