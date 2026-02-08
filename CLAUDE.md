# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**IL9Cast** - Illinois 9th District Democratic Primary Forecast aggregator for the March 17, 2026 primary. Aggregates prediction market data from Manifold Markets and Kalshi every **3 minutes** using a weighted formula, stores historical snapshots in JSONL format, applies multi-layer smoothing for clean charts, and serves an interactive web dashboard with Central Time display.

**Fresh Start:** All historical data from before Jan 30, 2026 has been purged. The system rebuilds from zero starting Jan 30 onward.

## Development Commands

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Run development server (with auto-reload)
python app.py

# Run production server locally (matches Railway)
gunicorn app:app --preload
```

### Data Management
```bash
# View recent snapshots (JSONL format)
tail -n 50 data/historical_snapshots.jsonl

# Check data file size
ls -lh data/historical_snapshots.jsonl

# Count total snapshots (without loading all into memory)
wc -l data/historical_snapshots.jsonl
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

**Backend:** Flask 2.3.2 + APScheduler for background data collection
- `app.py` (~950 lines) - Main Flask app with routes, API endpoints, data collection, smoothing pipelines
- Background task runs every **3 minutes** (not 1 minute) to fetch and aggregate market data
- Production: Gunicorn with `--preload` flag to ensure single scheduler instance
- Includes spike dampening (±3% per interval), EMA smoothing, RDP simplification, gap detection

**Frontend:** Server-side Jinja2 templates + Chart.js + Leaflet.js visualization
- `templates/landing_new.html` - Homepage hero section
- `templates/markets.html` - Live prediction market aggregation with Central Time display
- `templates/methodology.html` - Technical documentation with animated foldouts (4 sections)
- `templates/odds.html` - Precinct Model page with interactive Leaflet map, Monte Carlo results, data tables
- `templates/fundraising.html` - Fundraising data page (in development)
- `templates/about.html` - About page
- `static/style.css` (~2500 lines) - Complete styling with dark mode toggle

### Data Collection Pipeline

**Every 3 minutes:**
1. Fetch from Manifold API (`/v0/slug/who-will-win-the-democratic-primary-RZdcps6dL9`)
2. Fetch from Kalshi API (`/trade-api/v2/markets?series_ticker=KXIL9D&status=open`)
3. Normalize candidate names across platforms (handle prefixes, suffixes, variations)
4. **Aggregate using weighted formula:**
   - Manifold: 40%
   - Kalshi last price: 42%
   - Kalshi midpoint (bid/ask): 12% (falls back to `last_price` if `yes_bid = 0`)
   - Kalshi liquidity-adjusted: 6% (falls back to `last_price` if `yes_bid = 0`)
5. **Apply soft normalization** (30% strength toward sum=100%)
6. **Spike dampen:** Cap per-candidate change to ±3% per interval (prevents thin-market artifacts)
7. Create snapshot object with UTC timestamp (Z suffix)
8. **Atomically append to JSONL** (temp file → rename pattern)

**Important:** If both APIs fail, skip the snapshot entirely (no bad data saved).

### Thin-Market Fallback (Critical Fix)

When a candidate has **no yes-side bids** (`yes_bid = 0`), both **Midpoint** and **Liquidity-Weighted** price fall back to `last_price` instead of using the formula.

**Why:** The midpoint formula `(0 + yes_ask) / 2` produces massively inflated values. Example: Mike Simmons with `yes_bid=0, yes_ask=19, last_price=1` would incorrectly show Midpoint=9.5% and Liquidity≈9% instead of the correct ~1%. Fixed in both backend (`app.py:718-736`) and frontend (`markets.html:1584-1596`).

### Data Storage

**Critical:** All historical data stored in `data/historical_snapshots.jsonl` (JSONL format)

- **Format:** JSON Lines (one snapshot per line, newline-delimited)
- **Structure per line:**
  ```json
  {"candidates": [{"name": "Daniel Biss", "probability": 63.6, "hasKalshi": true}], "timestamp": "2026-01-30T14:30:00Z"}
  ```
- **Location:** Railway persistent volume mounted at `/app/data`
- **Growth rate:** ~1.4 MB/day at 480 snapshots/day (3-minute intervals)
- **Git Tracking:** Runtime data excluded, `seed_snapshots.json` tracked for initialization

**Why JSONL (not a database)?**
- Append-only writes (temp file + atomic `os.replace()`, no read-rewrite)
- Corruption-proof (each line is self-contained)
- Human-readable (can `tail` to inspect)
- One unparseable line doesn't corrupt others
- Vastly simpler than managing database connections

**Atomic Write Pattern** (`app.py:68-107`):
1. Write new snapshot to `historical_snapshots.jsonl.tmp`
2. Read existing file content
3. Write existing + new line to temp file
4. `os.replace()` swaps temp into place (atomic at OS level)
5. If crash occurs, either old or new file exists — never half-written

**Data Purge on Startup** (`app.py:241-303`):
- `purge_old_data()` runs once per Railway restart
- Removes all snapshots before Jan 30, 2026
- Deletes legacy `.json` and `.backup.*` files
- Creates `.purge_pre_jan30_done` marker to avoid re-running

### Chart Smoothing Pipeline

**Multiple layers of smoothing** to prevent spiky/jerky charts:

1. **Spike Dampening (data collection level):** Cap per-candidate change to ±3% per 3-minute interval (`app.py:662-682`)
2. **Exponential Moving Average (server-side):** EMA(alpha=0.15) applied per candidate before RDP (`app.py:555-575`). Each data point = 15% raw + 85% previous smoothed. Kills jitter while preserving trends.
3. **Ramer-Douglas-Peucker Simplification:** Recursively removes points closer than epsilon=0.5 from the trend line. Reduces ~3,360 raw points/week to ~200-400 visual points. (`app.py:156-182`)
4. **Monotone Cubic Interpolation:** Frontend setting `cubicInterpolationMode: 'monotone'` prevents overshoot between points. No fake dips/peaks. (`templates/markets.html:1375`)
5. **Tension 0.5:** Curviness parameter for smooth lines without over-smoothing. (`templates/markets.html:1376`)

**RDP Algorithm Intuition:** Draw a line from first to last point. Find the intermediate point farthest from that line. If farther than epsilon, keep it and recurse on both halves. Otherwise, that segment is "flat enough" to skip intermediate points.

### Central Time Display

All timestamps displayed in **Central Time (CT)** using `Intl.DateTimeFormat` with timezone `America/Chicago`. Handles DST automatically.

**Implementation:**
```javascript
function formatCentralTime(date, options = {}) {
    const defaults = { timeZone: 'America/Chicago', hour12: true };
    const formatter = new Intl.DateTimeFormat('en-US', { ...defaults, ...options });
    return formatter.format(date) + ' CT';
}
```

Applied to:
- Manifold update timestamp (markets.html:1197-1201)
- Chart tooltip titles (markets.html:1470-1474)
- Kalshi update timestamp (markets.html:1654-1657)
- X-axis tick labels (markets.html:1527-1537)

### API Endpoints

**Public JSON APIs**
- `GET /api/manifold` - Proxy to Manifold market data
- `GET /api/kalshi` - Proxy to Kalshi market data
- `GET /api/snapshots` - Full historical snapshot data (JSONL format)
- `GET /api/snapshots/chart?period={1d|7d|all}&epsilon=0.5` - RDP-simplified chart data with gaps. Uses 60-second in-memory cache.
- `POST /api/snapshot` - Save new snapshot (internal use by scraper)
- `GET /api/download/snapshots` - Download all historical data as JSONL file

**Page Routes**
- `GET /` - Landing page (landing_new.html)
- `GET /markets` - Live markets aggregation (markets.html)
- `GET /odds` - Precinct Model with interactive map & simulation results (odds.html)
- `GET /fundraising` - Fundraising data (fundraising.html, in development)
- `GET /methodology` - Technical methodology with 4 foldout sections (methodology.html)
- `GET /about` - About page (about.html)
- `GET /model/methodology` - Serves model methodology PDF (static/model/methodology.pdf)

## Deployment Configuration

**Railway (Primary Platform):**
- Builder: NIXPACKS
- Start: `gunicorn app:app --preload`
- Health check: `GET /` with 100s timeout
- Persistent volume: `/app/data` for historical snapshots
- Auto-restart: ON_FAILURE, up to 10 retries
- Environment: Python 3.x, port 8000 (or `$PORT`)

**Why `--preload` flag?**
- Loads Flask app once in master process before workers fork
- Ensures background scheduler thread only exists once
- Without it: N workers = N duplicate data collection threads

**Path Resolution** (`app.py:17-27`):
Checks in order: `/data`, `/app/data`, then local `data/` directory. Works on Railway and locally without env vars.

## Important Technical Details

### Background Task Scheduling

**Local Development** uses APScheduler BackgroundScheduler (in-process background thread).

**Production (Gunicorn)** uses plain `threading.Thread`:
```python
if 'gunicorn' in sys.argv[0]:
    # Start scheduler in background thread (--preload ensures only once)
```

This prevents worker processes from each spawning their own scheduler.

### Data Collection Loop Steps

1. Fetch Manifold (10s timeout)
2. Fetch Kalshi (10s timeout)
3. Both APIs failed? Skip snapshot entirely (no bad data)
4. Normalize candidate names
5. Aggregate using weighted formula
6. Soft normalize (30% strength)
7. **Spike dampen** (compare to previous, cap ±3%)
8. Atomically append to JSONL
9. Update in-memory `_last_snapshot` cache (used by spike dampening)

### Name Normalization & Cleaning

**Normalization** (`app.py:834-856`): Used for cross-platform matching
- Remove "Wil"/"Will" prefixes
- Remove "Dr." prefix
- Remove suffixes like "be the democratic nominee", "for IL-9", "win"
- Handle variations (e.g., "Kat Abughazaleh" ↔ "Katheryn Abughazaleh")

**Cleaning** (`app.py:858-867`): Used for display
- Remove prefixes/suffixes with case-insensitive matching
- Keeps proper name casing

### Chart Data Caching

In-memory cache (`_chart_cache`) stores `{data, time, key}`:
- Key = `{period}:{epsilon}` (e.g., `"7d:0.5"`)
- 60-second TTL: if same request within 60 sec, serve cached version
- Prevents re-running RDP on unchanged data

### Failure Modes & Defenses

| Issue | Defense |
|-------|---------|
| API timeout | 10-second timeout on both Manifold & Kalshi |
| Only one API works | Proceed with available data; spike dampening prevents weight shift artifacts |
| Railway restart | Persistent volume preserves all data |
| Corrupt JSONL line | Reader skips unparseable lines; doesn't corrupt others |
| Duplicate schedulers | `--preload` + `sys.argv` check ensures one scheduler thread |
| Data gap > 2 hours | Detected and marked with dashed lines (real outage, not normal 3-min intervals) |
| Thin-market prices | Fallback to `last_price` when `yes_bid = 0` |

### Dependencies

Only 5 production packages:
```
Flask==2.3.2         # Web framework
Werkzeug==2.3.6      # WSGI utilities
Requests==2.31.0     # HTTP client for APIs
Gunicorn==21.2.0     # Production WSGI server
APScheduler==3.10.4  # Background scheduling (dev mode)
```

No NumPy, Pandas, or heavyweight libraries. EMA, RDP, and aggregation are hand-written Python (~100 lines).

## UI & Documentation

### Precinct Model Page (`/odds`)

Interactive precinct-level Monte Carlo simulation model:
- **Headline cards**: Win probabilities (Biss ~60%, Fine ~30%, Abughazaleh ~10%) with colored accents
- **Average vote share bars**: Horizontal bars for all 7 candidates
- **Interactive Leaflet map**: 436 matched precincts from GeoJSON, dark CARTO tiles
  - 6 layer modes: Winner, Margin, Competitiveness, Biss/Fine/Abughazaleh heat maps
  - Rich hover tooltips with mini bar charts for all candidates
  - Click-to-select info panel with full precinct details
  - Layer switching controls and dynamic legends
  - `isolation: isolate` on `.map-container` prevents z-index overlap with sticky header
- **Model Visualizations**: 6 PNG graphs in single-column layout (92% max-width)
- **Detailed Precinct Data** (collapsible):
  - Competitiveness breakdown grid (5 buckets)
  - Top 10 Battleground Precincts table
  - Full sortable/filterable table of all 436 precincts with search and winner filter
- **Methodology section**: Links to PDF at `/model/methodology` with note that paper was drafted by Claude and reviewed/edited by the user
- **Mobile note**: Banner suggesting desktop viewing for best map experience
- **Data**: GeoJSON and PNGs served from `static/model/`, hardcoded summary stats (updated manually when model reruns)

### Methodology Page (4 Animated Foldouts)

Each foldout uses CSS `grid-template-rows: 0fr → 1fr` animation with cubic-bezier easing:

1. **Prediction Markets Aggregation** — Weights, formulas, thin-market fallback, Simmons example, chart smoothing explanation
2. **Precinct Model** — Links to model page (`/odds`) and methodology PDF (`/model/methodology`)
3. **Fundraising Analysis** — Coming soon (links to `/fundraising`)
4. **Infrastructure & Technical Stack** — Railway, persistent volumes, JSONL, schedulers, chart pipeline, frontend rendering, failure modes

### Navigation

All templates use consistent nav order: Markets | Candidates | Fundraising | Updates | Methodology | About | **Model** (rightmost, signifying primary feature)

Templates with navigation:
- landing_new.html, markets.html, odds.html, about.html, methodology.html, fundraising.html, candidates.html, candidate_fundraising.html, case_study_bid_ask.html, updates.html
- base.html (different `<li>` structure with dropdown for Developing section)

### Chart Footer Notes

**Chart subtitle** mentions:
- Jan 15–30 data available on request (AWS volume issues prevented display)
- Dashed lines = Railway/AWS outages (>2 hour gaps)
- All times in Central Time (CT)

## File Structure

### Key Files
- `app.py` — Main Flask app with all logic
  - `collect_market_data()` (line 684) — 3-minute scraper with spike dampening
  - `append_snapshot_jsonl()` (line 68) — Atomic JSONL write
  - `read_snapshots_jsonl()` (line 41) — Load all snapshots
  - `purge_old_data()` (line 241) — One-time cleanup of pre-Jan-30 data
  - `get_snapshots_chart()` (line 487) — EMA + RDP pipeline
  - `rdp_simplify()` (line 156) — Ramer-Douglas-Peucker algorithm
  - `_dampen_spikes()` (line 662) — Cap per-candidate change

- `templates/odds.html` — Precinct Model page with Leaflet map, data tables, graph gallery
- `templates/methodology.html` — 4-section foldout UI with infrastructure docs
- `templates/markets.html` — Markets page with Central Time formatting
- `static/model/il9_precinct_model.geojson` — GeoJSON with 436 matched + 109 unmatched precincts (4.6MB)
- `static/model/methodology.pdf` — Model methodology paper
- `static/model/*.png` — Model visualization graphs (6 files)
- `Procfile` — Railway start command
- `railway.toml` — Railway config (volume mount, health check, restart policy)
- `requirements.txt` — Python dependencies
- `data/historical_snapshots.jsonl` — JSONL data (persisted on Railway volume)
- `data/seed_snapshots.json` — Git-tracked seed (initialization only)

### Markers (Do Not Delete)
- `data/.purge_pre_jan30_done` — Prevents re-running data purge
- `data/.timezone_reset_v*` — Prevents re-running timezone migrations

## Common Tasks

### Inspecting Data
```bash
# View last 20 snapshots
tail -n 20 data/historical_snapshots.jsonl

# Count total snapshots
wc -l data/historical_snapshots.jsonl

# Check specific timestamp
grep "2026-01-30T15" data/historical_snapshots.jsonl

# Pretty-print one line
tail -1 data/historical_snapshots.jsonl | python3 -m json.tool
```

### Testing Locally
```bash
# Dev mode (Flask auto-reload)
python app.py

# Production mode (Gunicorn, matches Railway)
gunicorn app:app --preload
# Visit http://localhost:8000

# Test data collection cycle
python -c "from app import collect_market_data; collect_market_data()"
```

### Debugging Railway
```bash
# Stream live logs
railway logs -f

# SSH into container
railway shell

# Check persistent volume
ls -lh /app/data/
```

## Git Workflow

- Main branch: `main` (production)
- Auto-deploy: Every push to `main` triggers Railway build
- Atomic commits preferred (one feature/fix per commit)
- All changes require CLAUDE.md updates if they affect:
  - Data flow/API
  - Configuration
  - Methodology/formulas
  - Infrastructure

## Recent Major Changes (Jan 2026)

1. **Fresh Start from Jan 30** — All pre-Jan-30 data purged on startup
2. **3-Minute Intervals** — Changed from 1-minute to 3-minute collection (was over-sampling)
3. **Spike Dampening** — Cap ±3% per interval per candidate (prevents thin-market artifacts)
4. **Multi-Layer Smoothing** — EMA + RDP + monotone splines for smooth, trustworthy charts
5. **Thin-Market Fallback** — Fallback to `last_price` when `yes_bid = 0` (fixes Simmons example)
6. **Central Time Display** — All timestamps shown in CT via `Intl.DateTimeFormat`
7. **2-Hour Gap Threshold** — Only major outages trigger dashed lines (was 1 hour)
8. **Methodology Foldouts** — 4-section interactive documentation on `/methodology` page
9. **Infrastructure Docs** — Deep-dive on Railway, JSONL, schedulers, chart pipeline
10. **Fundraising Nav** — Added Fundraising link to all page navs

## Recent Major Changes (Feb 2026)

1. **Precinct Model Page** — Interactive Leaflet.js map with 436 precincts, Monte Carlo simulation results (100K sims), 6 layer modes, rich tooltips, sortable data tables, and PNG graph gallery
2. **Model Methodology PDF** — Served at `/model/methodology` via Flask `send_file` route
3. **Nav Reorder** — "Model" link moved to rightmost position across all templates to signify primary feature
4. **Methodology Foldout Updated** — Section 2 changed from "Forecast Model (Coming Soon)" to "Precinct Model" with links to model page and methodology PDF

