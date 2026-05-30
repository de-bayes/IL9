#!/usr/bin/env python3
"""Patch CLAUDE.md with performance architecture docs (idempotent)."""
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "CLAUDE.md"
t = p.read_text()

replacements = [
    (
        "# Run production server locally (matches Railway)\ngunicorn app:app --preload\n```",
        "# Run production server locally (matches Railway)\ngunicorn app:app --config gunicorn.conf.py\n\n"
        "# Performance smoke tests (server must be running on :8000)\npython scripts/smoke_test_perf.py\n```",
    ),
    (
        "- Production: Gunicorn with `--preload` flag to ensure single scheduler instance\n"
        "- Includes spike dampening",
        "- Production: Gunicorn via `gunicorn.conf.py` (`preload_app`, `gthread` workers)\n"
        "- `performance.py` — security headers, gzip JSON/text, immutable cache for `?v=` static\n"
        "- **Archive mode:** scraper removed; JSONL read-only; chart cache pre-warms at import\n"
        "- Includes spike dampening",
    ),
    (
        "- `GET /api/snapshots/chart?period={1d|7d|all}&epsilon=0.5` - RDP-simplified chart data with gaps. Uses 60-second in-memory cache.",
        "- `GET /api/snapshots/chart?period={1d|7d|all}&epsilon=0.5` - RDP chart data; cache keyed by JSONL file size; ETag / 304; `get_jsonl_raw_lines()`\n"
        "- `GET /healthz` - Lightweight JSON health check for Railway\n"
        "- `GET /api/model/precincts` - Precinct GeoJSON; gzip body when client accepts encoding",
    ),
    (
        "- Start: `gunicorn app:app --preload`\n- Health check: `GET /` with 100s timeout",
        "- Start: `gunicorn app:app --config gunicorn.conf.py` (see `Procfile`, `railway.toml`)\n"
        "- Health check: `GET /healthz` with 100s timeout",
    ),
    (
        "**Why `--preload` flag?**\n"
        "- Loads Flask app once in master process before workers fork\n"
        "- Ensures background scheduler thread only exists once\n"
        "- Without it: N workers = N duplicate data collection threads",
        "**Why `preload_app` in `gunicorn.conf.py`?**\n"
        "- Loads Flask once before workers fork; chart/JSONL caches shared via copy-on-write\n"
        "- `gthread` workers serve concurrent API requests without blocking each other",
    ),
    (
        "- `static/model/il9_precinct_model.geojson` — GeoJSON with 436 matched + 109 unmatched precincts (4.6MB)\n"
        "- `static/model/methodology.pdf`",
        "- `static/model/il9_precinct_model.geojson` — GeoJSON (~1.6MB)\n"
        "- `static/model/il9_precinct_model.geojson.gz` — Precompressed for `/api/model/precincts` (~250KB)\n"
        "- `gunicorn.conf.py`, `performance.py`, `scripts/smoke_test_perf.py`\n"
        "- `static/model/methodology.pdf`",
    ),
]

for old, new in replacements:
    if old in t and new not in t:
        t = t.replace(old, new, 1)

if "## Recent Major Changes (May 2026)" not in t:
    t = t.rstrip() + """

## Recent Major Changes (May 2026)

1. **538 Editorial UI** — Fluid layout, responsive nav, shared utilities in `landing-style.css`
2. **Performance stack** — `gunicorn.conf.py`, `performance.py`, `/healthz`, JSONL line cache, sync chart prewarm, ETag 304s
3. **Precinct API** — `GET /api/model/precincts` with gzip; deferred Leaflet + resource hints on `/odds`
4. **Markets prefetch** — Chart API preloaded from `<head>`; responsive chart height and scrollable period toggles
"""

p.write_text(t)
print("CLAUDE.md updated")
